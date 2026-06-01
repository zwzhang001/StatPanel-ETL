from __future__ import annotations

import inspect
import sys
import traceback
from pathlib import Path
from typing import Callable

import pandas as pd
from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "Step3Core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from setl_core import (  # noqa: E402
    CrawlerConfig,
    ExtractRule,
    apply_supplement_file,
    create_model_file,
    crawl_cnki,
    export_quality_report,
    extract_indicator_file,
    interpolate_file,
    merge_by_indicator,
    plot_trend_file,
    standardize_sample,
    convert_encoding,
)
import setl_core.crawler as crawler_core  # noqa: E402
from setl_core.crawler import CNKI_INDICATOR_SEARCH_URL, CNKI_STAT_SEARCH_URL, search_cnki_indicators  # noqa: E402
from setl_core.transform import long_to_wide_file, wide_to_long_file  # noqa: E402


APP_STYLE = """
QWidget {
    font-family: "Times New Roman";
    font-size: 12pt;
}
QMainWindow { background: #f6f7fb; }
QMenuBar { background: #ffffff; padding: 4px; border-bottom: 1px solid #d8dce6; }
QMenuBar::item { padding: 6px 12px; }
QGroupBox {
    background: #ffffff;
    border: 1px solid #d8dce6;
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QTableWidget {
    background: #ffffff;
    border: 1px solid #cbd2df;
    border-radius: 4px;
    padding: 5px;
}
QPushButton {
    background: #2454a6;
    color: #ffffff;
    border: 0;
    border-radius: 4px;
    padding: 7px 12px;
}
QPushButton:hover { background: #1d478e; }
QPushButton:disabled { background: #9aa7bd; }
QLabel#Title { font-size: 12pt; font-weight: 700; color: #18233a; }
QLabel#Subtitle { color: #526070; }
"""


class Worker(QObject):
    log = pyqtSignal(str)
    preview = pyqtSignal(object)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable[..., object]):
        super().__init__()
        self.func = func

    def run(self) -> None:
        try:
            self.log.emit("Task started.")
            parameter_count = len(inspect.signature(self.func).parameters)
            if parameter_count >= 2:
                result = self.func(self.log.emit, self.preview.emit)
            else:
                result = self.func()
            self.done.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class BasePage(QWidget):
    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.fields: dict[str, QWidget] = {}
        self.thread: QThread | None = None
        self.worker: Worker | None = None

        root = QVBoxLayout(self)
        header = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("Title")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Subtitle")
        subtitle_label.setWordWrap(True)
        header.addWidget(title_label)
        header.addWidget(subtitle_label)
        root.addLayout(header)

        main = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        self.left_layout = left
        self.right_layout = right

        self.form_group = QGroupBox("Parameters")
        self.form = QFormLayout(self.form_group)
        left.addWidget(self.form_group)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_task)
        button_row.addWidget(self.start_button)
        left.addLayout(button_row)
        left.addStretch()

        self.preview = QTableWidget()
        self.preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(self.preview_group)
        preview_layout.addWidget(self.preview)
        right.addWidget(self.preview_group, 3)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(self.log_group)
        log_layout.addWidget(self.log_box)
        right.addWidget(self.log_group, 2)

        main.addLayout(left, 2)
        main.addLayout(right, 3)
        root.addLayout(main)

    def add_path(self, key: str, label: str, mode: str) -> QLineEdit:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        button = QPushButton("Select")
        button.clicked.connect(lambda: self.pick_path(edit, mode))
        layout.addWidget(edit)
        layout.addWidget(button)
        self.form.addRow(label, row)
        self.fields[key] = edit
        return edit

    def add_text(self, key: str, label: str, default: str = "") -> QLineEdit:
        edit = QLineEdit(default)
        self.form.addRow(label, edit)
        self.fields[key] = edit
        return edit

    def add_spin(self, key: str, label: str, default: int = 2000, minimum: int = 0, maximum: int = 9999) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        self.form.addRow(label, spin)
        self.fields[key] = spin
        return spin

    def add_combo(self, key: str, label: str, values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        self.form.addRow(label, combo)
        self.fields[key] = combo
        return combo

    def add_check(self, key: str, label: str, default: bool = False) -> QCheckBox:
        check = QCheckBox()
        check.setChecked(default)
        self.form.addRow(label, check)
        self.fields[key] = check
        return check

    def add_left_widget(self, widget: QWidget) -> None:
        self.left_layout.insertWidget(max(0, self.left_layout.count() - 1), widget)

    def pick_path(self, edit: QLineEdit, mode: str) -> None:
        if mode == "file":
            path, _ = QFileDialog.getOpenFileName(self, "Select file", "", "Data files (*.csv *.xlsx *.xls);;All files (*.*)")
        elif mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "Select output file", "", "CSV (*.csv);;Excel (*.xlsx);;All files (*.*)")
        elif mode == "image_save":
            path, _ = QFileDialog.getSaveFileName(self, "Select output image", "", "PNG Image (*.png);;JPEG Image (*.jpg);;All files (*.*)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Select directory")
        if path:
            edit.setText(path)

    def text(self, key: str) -> str:
        widget = self.fields[key]
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        raise TypeError(key)

    def integer(self, key: str) -> int:
        widget = self.fields[key]
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        return int(self.text(key))

    def checked(self, key: str) -> bool:
        widget = self.fields[key]
        return bool(widget.isChecked()) if isinstance(widget, QCheckBox) else False

    def append_log(self, message: str) -> None:
        self.log_box.append(message)

    def show_table(self, path: str | Path | None) -> None:
        if not path:
            return
        file_path = Path(path)
        if file_path.is_dir():
            files = sorted(file_path.glob("*.csv"))
            if not files:
                self.append_log(f"No CSV preview found in {file_path}")
                return
            file_path = files[0]
        try:
            if file_path.suffix.lower() in {".xlsx", ".xls"}:
                df = pd.read_excel(file_path, nrows=100)
            else:
                last_error: Exception | None = None
                for encoding in ("utf-8", "utf-8-sig", "gb18030", "gb2312"):
                    try:
                        df = pd.read_csv(file_path, nrows=100, encoding=encoding)
                        break
                    except UnicodeDecodeError as exc:
                        last_error = exc
                else:
                    raise last_error or ValueError(f"Unable to read CSV: {file_path}")
        except Exception as exc:
            self.append_log(f"Preview skipped: {exc}")
            return

        self.preview.clear()
        self.preview.setRowCount(len(df))
        self.preview.setColumnCount(len(df.columns))
        self.preview.setHorizontalHeaderLabels([str(col) for col in df.columns])
        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, value in enumerate(row):
                self.preview.setItem(row_idx, col_idx, QTableWidgetItem("" if pd.isna(value) else str(value)))
        self.preview.resizeColumnsToContents()

    def task(self) -> object:
        raise NotImplementedError

    def start_task(self) -> None:
        self.start_button.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(self.task)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.preview.connect(self.on_preview)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.done.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self.start_button.setEnabled(True))
        self.thread.start()

    def on_done(self, result: object) -> None:
        self.append_log(f"Task finished: {result}")
        self.show_table(result if isinstance(result, (str, Path)) else None)
        QMessageBox.information(self, "Task finished", f"Output:\n{result}")

    def on_preview(self, event: object) -> None:
        if isinstance(event, dict):
            path = event.get("path")
            if path:
                self.show_table(path)

    def on_failed(self, error: str) -> None:
        self.append_log(error)
        QMessageBox.critical(self, "Task failed", error.splitlines()[-1] if error else "Unknown error")


class ModelPage(BasePage):
    def __init__(self):
        super().__init__("Model", "Create a city-year-index panel template from a city list.")
        self.add_path("city_file", "City Name File", "file")
        self.add_path("output_file", "Save Path", "save")
        self.add_spin("start_year", "Start Year", 2000)
        self.add_spin("end_year", "End Year", 2020)
        self.add_text("indexes", "Indexes", "GDP")

    def task(self) -> object:
        indexes = [item.strip() for item in self.text("indexes").split(",") if item.strip()]
        return create_model_file(
            self.integer("start_year"),
            self.integer("end_year"),
            self.text("city_file"),
            self.text("output_file"),
            indexes=indexes or None,
        )


class PreprocessPage(BasePage):
    def __init__(self):
        super().__init__("Crawler / Preprocess", "Prepare manually downloaded or crawler-produced CSV files.")
        self.add_combo("step", "Step", ["convert_encoding", "standardize_sample", "merge_by_indicator"])
        self.add_path("input_dir", "Input Directory", "dir")
        self.add_path("output_dir", "Output Directory", "dir")
        self.add_text("encoding", "From Encoding", "gb18030")

    def task(self) -> object:
        step = self.text("step")
        if step == "convert_encoding":
            convert_encoding(self.text("input_dir"), self.text("output_dir"), from_encoding=self.text("encoding") or "gb18030")
        elif step == "standardize_sample":
            standardize_sample(self.text("input_dir"), self.text("output_dir"))
        elif step == "merge_by_indicator":
            merge_by_indicator(self.text("input_dir"), self.text("output_dir"))
        else:
            standardize_sample(self.text("input_dir"), self.text("output_dir"))
        return Path(self.text("output_dir"))


class CrawlerPage(BasePage):
    def __init__(self):
        super().__init__(
            "Crawler",
            "Open CNKI in a visible browser for manual login, then crawl user-selected city/year/index data.",
        )
        self.start_button.hide()
        self.add_text("keyword", "Index Keyword", "GDP")
        self.add_path("city_file", "City File", "file")
        self.add_path("output_dir", "Output Directory", "dir")
        self.add_spin("start_year", "Start Year", 2000)
        self.add_spin("end_year", "End Year", 2020)
        self.add_text("login_url", "Login URL", CNKI_INDICATOR_SEARCH_URL)
        self.add_text("search_url", "Search URL", CNKI_STAT_SEARCH_URL)
        self.add_spin("login_wait_seconds", "Login Wait Seconds", 120, minimum=0, maximum=3600)
        self.add_spin("wait_seconds", "Page Wait Seconds", 5, minimum=1, maximum=300)
        self.add_text("encoding", "Output Encoding", "gb18030")
        self.add_check("resume", "Skip Existing Files", True)
        self.add_check("headless", "Headless Browser", False)

        self.indicator_thread: QThread | None = None
        self.indicator_worker: Worker | None = None
        self.search_indicator_button = QPushButton("Search Indicators")
        self.search_indicator_button.clicked.connect(self.start_indicator_search)
        self.crawl_button = QPushButton("Crawl")
        self.crawl_button.clicked.connect(self.start_task)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_crawl)
        self.stop_requested = False
        self.indicator_list = QListWidget()
        self.indicator_list.setMinimumHeight(180)
        indicator_group = QGroupBox("Matched Indicators")
        indicator_layout = QVBoxLayout(indicator_group)
        indicator_layout.addWidget(self.search_indicator_button)
        indicator_layout.addWidget(self.indicator_list)
        crawl_row = QHBoxLayout()
        crawl_row.addWidget(self.crawl_button)
        crawl_row.addWidget(self.stop_button)
        indicator_layout.addLayout(crawl_row)
        self.add_left_widget(indicator_group)

    def start_task(self) -> None:
        selected_indexes = self.selected_indicators()
        if not selected_indexes:
            QMessageBox.warning(self, "No indicators selected", "Please search indicators and select at least one exact indicator first.")
            return
        self.stop_requested = False
        self.crawl_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        super().start_task()
        if self.thread:
            self.thread.finished.connect(lambda: self.crawl_button.setEnabled(True))
            self.thread.finished.connect(lambda: self.stop_button.setEnabled(False))

    def stop_crawl(self) -> None:
        self.stop_requested = True
        self.stop_button.setEnabled(False)
        self.append_log("Stop requested. The crawler will stop after the current city or indicator finishes.")

    def start_indicator_search(self) -> None:
        QMessageBox.information(
            self,
            "CNKI login required",
            "A browser window will open. Please complete CNKI login, VPN/IP verification, or captcha there. "
            "The candidate indicators will be loaded after the configured login wait time.",
        )
        self.search_indicator_button.setEnabled(False)
        self.indicator_thread = QThread()
        self.indicator_worker = Worker(self.search_indicator_task)
        self.indicator_worker.moveToThread(self.indicator_thread)
        self.indicator_thread.started.connect(self.indicator_worker.run)
        self.indicator_worker.log.connect(self.append_log)
        self.indicator_worker.done.connect(self.on_indicator_search_done)
        self.indicator_worker.failed.connect(self.on_failed)
        self.indicator_worker.done.connect(self.indicator_thread.quit)
        self.indicator_worker.failed.connect(self.indicator_thread.quit)
        self.indicator_thread.finished.connect(self.indicator_worker.deleteLater)
        self.indicator_thread.finished.connect(self.indicator_thread.deleteLater)
        self.indicator_thread.finished.connect(lambda: self.search_indicator_button.setEnabled(True))
        self.indicator_thread.start()

    def search_indicator_task(self, progress: Callable[[str], None] | None = None, preview: Callable[[object], None] | None = None) -> object:
        config = self.build_config(selected_indexes=None)
        return search_cnki_indicators(config, progress=progress)

    def on_indicator_search_done(self, result: object) -> None:
        indicators = result if isinstance(result, list) else []
        self.indicator_list.clear()
        for indicator in indicators:
            if isinstance(indicator, dict):
                label = str(indicator.get("name") or indicator.get("query_name") or indicator.get("code") or "")
                payload = indicator
            else:
                label = str(indicator)
                payload = label
            if not label:
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, payload)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.indicator_list.addItem(item)
        self.append_log(f"Indicator candidates loaded: {len(indicators)}")

    def selected_indicators(self) -> list[object]:
        indicators: list[object] = []
        for row in range(self.indicator_list.count()):
            item = self.indicator_list.item(row)
            if item.checkState() == Qt.Checked:
                indicators.append(item.data(Qt.UserRole) or item.text())
        return indicators

    def build_config(self, selected_indexes: list[object] | None) -> CrawlerConfig:
        return CrawlerConfig(
            keyword=self.text("keyword"),
            city_file=self.text("city_file"),
            start_year=self.integer("start_year"),
            end_year=self.integer("end_year"),
            output_dir=self.text("output_dir"),
            login_url=self.text("login_url") or CNKI_INDICATOR_SEARCH_URL,
            search_url=self.text("search_url") or CNKI_STAT_SEARCH_URL,
            selected_indexes=selected_indexes,
            login_wait_seconds=self.integer("login_wait_seconds"),
            wait_seconds=float(self.integer("wait_seconds")),
            output_encoding=self.text("encoding") or "gb18030",
            resume=self.checked("resume"),
            headless=self.checked("headless"),
        )

    def task(self, progress: Callable[[str], None] | None = None, preview: Callable[[object], None] | None = None) -> object:
        config = self.build_config(selected_indexes=self.selected_indicators())
        if progress:
            progress(f"Using crawler core: {Path(crawler_core.__file__).resolve()}")
            progress(f"Crawl URL: {config.search_url}")
            labels = []
            for item in config.selected_indexes or []:
                if isinstance(item, dict):
                    labels.append(str(item.get("name") or item.get("query_name") or item.get("code") or ""))
                else:
                    labels.append(str(item))
            progress(f"Selected indicators: {', '.join(label for label in labels if label)}")
            progress(f"Year range: {config.start_year}-{config.end_year}")
        return crawl_cnki(config, progress=progress, preview=preview, cancel_requested=lambda: self.stop_requested)

    def on_preview(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        path = event.get("path")
        if not path:
            return
        self.show_table(path)
        city = event.get("city", "")
        row_count = event.get("row_count")
        skipped = event.get("skipped")
        if skipped:
            self.append_log(f"Preview refreshed from existing file: {city} -> {path}")
        else:
            self.append_log(f"Preview refreshed: {city}, rows: {row_count}, file: {path}")

    def on_done(self, result: object) -> None:
        if hasattr(result, "downloaded_files"):
            downloaded = getattr(result, "downloaded_files")
            failed = getattr(result, "failed_items")
            log_file = getattr(result, "log_file")
            self.append_log(f"Crawler finished. Downloaded: {len(downloaded)}; failed: {len(failed)}; log: {log_file}")
            if downloaded:
                self.show_table(downloaded[0])
            QMessageBox.information(
                self,
                "Crawler finished",
                f"Downloaded files: {len(downloaded)}\nFailed items: {len(failed)}\nLog file:\n{log_file}",
            )
            return
        super().on_done(result)


class ExtractPage(BasePage):
    def __init__(self):
        super().__init__("Extract", "Extract one indicator from a preprocessed sample file into a model template.")
        self.add_path("model_file", "ExtractPath / Model File", "file")
        self.add_path("sample_file", "Sample File", "file")
        self.add_path("output_file", "Save Path", "save")
        self.add_text("index", "Index Name", "GDP")
        self.add_text("unit", "Unit", "")
        self.add_combo("source_mode", "Source Rule", ["any", "exact", "contains", "city_contains"])
        self.add_text("source", "Source Keyword", "")
        self.add_combo("prefer", "Multi-match Policy", ["error", "first", "last", "non_null_first"])
        self.add_check("fuzzy_index", "Fuzzy Index", False)

    def task(self) -> object:
        rule = ExtractRule(
            index=self.text("index"),
            unit=self.text("unit") or None,
            source=self.text("source") or None,
            source_mode=self.text("source_mode"),
            fuzzy_index=self.checked("fuzzy_index"),
            prefer=self.text("prefer"),
        )
        return extract_indicator_file(self.text("model_file"), self.text("sample_file"), self.text("output_file"), rule)


class QualityPage(BasePage):
    def __init__(self):
        super().__init__("Quality Report", "Calculate missing rates and export supplement/delete suggestions.")
        self.add_path("input_file", "FilePath", "file")
        self.add_path("output_path", "Save Path", "save")
        self.add_text("city_threshold", "City Missing Threshold", "0.8")
        self.add_text("index_threshold", "Index Missing Threshold", "0.8")

    def task(self) -> object:
        return export_quality_report(
            self.text("input_file"),
            self.text("output_path"),
            city_threshold=float(self.text("city_threshold")),
            index_threshold=float(self.text("index_threshold")),
        )


class SupplementPage(BasePage):
    def __init__(self):
        super().__init__("Supplement", "Merge manual supplement values without overwriting existing values by default.")
        self.add_path("extract_file", "FilePath", "file")
        self.add_path("supplement_file", "SupplementPath", "file")
        self.add_path("output_file", "OutPath", "save")
        self.add_check("overwrite", "Allow Overwrite", False)

    def task(self) -> object:
        return apply_supplement_file(
            self.text("extract_file"),
            self.text("supplement_file"),
            self.text("output_file"),
            overwrite=self.checked("overwrite"),
        )


class InterpolationPage(BasePage):
    def __init__(self):
        super().__init__("Interpolation", "Fill missing values by city and index group, marking generated values.")
        self.add_path("input_file", "FilePath", "file")
        self.add_path("output_file", "Save Path", "save")
        self.add_combo("method", "Rule", ["linear", "ffill", "bfill", "growth"])

    def task(self) -> object:
        return interpolate_file(self.text("input_file"), self.text("output_file"), method=self.text("method"))


class TransformPage(BasePage):
    def __init__(self):
        super().__init__("Transform", "Convert between panel long tables and year-column wide tables.")
        self.add_combo("mode", "Mode", ["Panel2Contab (long_to_wide)", "Contab2Panel (wide_to_long)"])
        self.add_path("input_file", "FilePath", "file")
        self.add_path("output_file", "Save Path", "save")

    def task(self) -> object:
        if self.text("mode").startswith("Panel2Contab"):
            return long_to_wide_file(self.text("input_file"), self.text("output_file"))
        return wide_to_long_file(self.text("input_file"), self.text("output_file"))


class LoadPage(BasePage):
    def __init__(self):
        super().__init__("Load", "Select one variable and generate a general trend chart for quick inspection.")
        self.start_button.setText("Generate Trend Chart")
        self.add_path("input_file", "Data File", "file")
        self.add_path("output_file", "Chart Save Path", "image_save")
        self.add_text("variable", "Variable", "GDP")
        self.add_text("variable_column", "Variable Column", "Index")
        self.add_text("year_column", "Year Column", "Year")
        self.add_text("value_column", "Value Column", "Value")
        self.add_text("group_column", "Group Column", "")
        self.add_text("title", "Chart Title", "")

        self.chart_label = QLabel("Trend chart preview")
        self.chart_label.setAlignment(Qt.AlignCenter)
        self.chart_label.setMinimumHeight(320)
        self.chart_label.setStyleSheet("background: #ffffff; border: 1px solid #cbd2df; border-radius: 4px; color: #526070;")

        chart_group = QGroupBox("Trend Chart")
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.addWidget(self.chart_label)
        self.right_layout.removeWidget(self.preview_group)
        self.preview_group.setParent(None)
        self.right_layout.insertWidget(0, chart_group, 4)

    def task(self) -> object:
        return plot_trend_file(
            input_file=self.text("input_file"),
            output_file=self.text("output_file"),
            variable=self.text("variable"),
            variable_column=self.text("variable_column") or "Index",
            year_column=self.text("year_column") or "Year",
            value_column=self.text("value_column") or "Value",
            group_column=self.text("group_column") or None,
            title=self.text("title") or None,
        )

    def on_done(self, result: object) -> None:
        self.append_log(f"Task finished: {result}")
        if isinstance(result, (str, Path)):
            pixmap = QPixmap(str(result))
            if not pixmap.isNull():
                self.chart_label.setPixmap(pixmap.scaled(self.chart_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        QMessageBox.information(self, "Task finished", f"Output:\n{result}")


class PlaceholderPage(QWidget):
    def __init__(self, title: str, message: str):
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel(title)
        label.setObjectName("Title")
        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setObjectName("Subtitle")
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        inner = QVBoxLayout(frame)
        inner.addWidget(msg)
        inner.addStretch()
        layout.addWidget(label)
        layout.addWidget(frame)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StatPanel ETL")
        self.resize(1280, 800)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.pages: dict[str, QWidget] = {}
        self._build_pages()
        self._build_menu()
        self.statusBar().showMessage("Ready")

    def _add_page(self, key: str, page: QWidget) -> None:
        self.pages[key] = page
        self.stack.addWidget(page)

    def _build_pages(self) -> None:
        self._add_page("crawler", CrawlerPage())
        self._add_page("preprocess", PreprocessPage())
        self._add_page("model", ModelPage())
        self._add_page("extract", ExtractPage())
        self._add_page("quality", QualityPage())
        self._add_page("supplement", SupplementPage())
        self._add_page("transform", TransformPage())
        self._add_page("interpolation", InterpolationPage())
        self._add_page("load", LoadPage())

    def _build_menu(self) -> None:
        definitions = [
            ("Crawler", "crawler"),
            ("Preprocess", "preprocess"),
            ("Model", "model"),
            ("Extract", "extract"),
            ("Quality", "quality"),
            ("Supplement", "supplement"),
            ("Transform", "transform"),
            ("Interpolation", "interpolation"),
            ("Load", "load"),
        ]
        for label, key in definitions:
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, page_key=key: self.show_page(page_key))
            self.menuBar().addAction(action)

    def show_page(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.statusBar().showMessage(key)


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Times New Roman", 12))
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
