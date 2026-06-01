from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pandas as pd


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "Step4GUI"
WORK_DIR = ROOT / "Step5Test" / "gui_smoke_workspace"
sys.path.insert(0, str(GUI_DIR))

from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QSpinBox  # noqa: E402

from app import MainWindow  # noqa: E402


def set_field(page, key: str, value) -> None:
    widget = page.fields[key]
    if isinstance(widget, QLineEdit):
        widget.setText(str(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(value))
    elif isinstance(widget, QComboBox):
        index = widget.findText(str(value))
        if index < 0:
            raise AssertionError(f"Combo value not found for {key}: {value}")
        widget.setCurrentIndex(index)
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    else:
        raise AssertionError(f"Unsupported widget for {key}: {type(widget)}")


def reset_workspace() -> None:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    for name in ["model", "extract", "quality", "supplement", "interpolate", "transform", "load"]:
        (WORK_DIR / name).mkdir(parents=True, exist_ok=True)


def write_inputs() -> None:
    pd.DataFrame(
        [
            {"ProvinceName": "ProvinceA", "CityName": "Alpha", "no": "1001"},
            {"ProvinceName": "ProvinceB", "CityName": "Beta", "no": "1002"},
        ]
    ).to_csv(WORK_DIR / "city_list.csv", index=False, encoding="utf-8")

    pd.DataFrame(
        [
            {"Year": "2020", "Region": "Alpha", "Index": "GDP", "Value": "100", "Unit": "billion yuan", "Source": "Yearbook", "City_code": "1001"},
            {"Year": "2022", "Region": "Alpha", "Index": "GDP", "Value": "140", "Unit": "billion yuan", "Source": "Yearbook", "City_code": "1001"},
            {"Year": "2020", "Region": "Beta", "Index": "GDP", "Value": "200", "Unit": "billion yuan", "Source": "Yearbook", "City_code": "1002"},
            {"Year": "2021", "Region": "Beta", "Index": "GDP", "Value": "220", "Unit": "billion yuan", "Source": "Yearbook", "City_code": "1002"},
            {"Year": "2022", "Region": "Beta", "Index": "GDP", "Value": "240", "Unit": "billion yuan", "Source": "Yearbook", "City_code": "1002"},
        ]
    ).to_csv(WORK_DIR / "sample.csv", index=False, encoding="utf-8")

    pd.DataFrame(
        [
            {
                "no": "1001",
                "Year": "2021",
                "Index": "GDP",
                "Value": "120",
                "Unit": "billion yuan",
                "Source": "Manual supplement",
            }
        ]
    ).to_csv(WORK_DIR / "supplement.csv", index=False, encoding="utf-8")


def main() -> None:
    reset_workspace()
    write_inputs()

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    expected_pages = {"crawler", "preprocess", "model", "extract", "quality", "supplement", "transform", "interpolation", "load"}
    assert expected_pages.issubset(set(window.pages)), "MainWindow is missing expected pages"

    model = window.pages["model"]
    set_field(model, "city_file", WORK_DIR / "city_list.csv")
    set_field(model, "output_file", WORK_DIR / "model" / "model.csv")
    set_field(model, "start_year", 2020)
    set_field(model, "end_year", 2022)
    set_field(model, "indexes", "GDP")
    assert Path(model.task()).exists(), "Model page did not create output"

    extract = window.pages["extract"]
    set_field(extract, "model_file", WORK_DIR / "model" / "model.csv")
    set_field(extract, "sample_file", WORK_DIR / "sample.csv")
    set_field(extract, "output_file", WORK_DIR / "extract" / "GDP_extract.csv")
    set_field(extract, "index", "GDP")
    set_field(extract, "unit", "billion yuan")
    assert Path(extract.task()).exists(), "Extract page did not create output"

    quality = window.pages["quality"]
    set_field(quality, "input_file", WORK_DIR / "extract" / "GDP_extract.csv")
    set_field(quality, "output_path", WORK_DIR / "quality" / "quality_report.xlsx")
    assert Path(quality.task()).exists(), "Quality page did not create output"

    supplement = window.pages["supplement"]
    set_field(supplement, "extract_file", WORK_DIR / "extract" / "GDP_extract.csv")
    set_field(supplement, "supplement_file", WORK_DIR / "supplement.csv")
    set_field(supplement, "output_file", WORK_DIR / "supplement" / "GDP_supplemented.csv")
    assert Path(supplement.task()).exists(), "Supplement page did not create output"

    interpolation = window.pages["interpolation"]
    set_field(interpolation, "input_file", WORK_DIR / "supplement" / "GDP_supplemented.csv")
    set_field(interpolation, "output_file", WORK_DIR / "interpolate" / "GDP_interpolated.csv")
    assert Path(interpolation.task()).exists(), "Interpolation page did not create output"

    transform = window.pages["transform"]
    set_field(transform, "mode", "Panel2Contab (long_to_wide)")
    set_field(transform, "input_file", WORK_DIR / "interpolate" / "GDP_interpolated.csv")
    set_field(transform, "output_file", WORK_DIR / "transform" / "GDP_wide.csv")
    assert Path(transform.task()).exists(), "Transform page did not create wide output"

    load = window.pages["load"]
    set_field(load, "input_file", WORK_DIR / "interpolate" / "GDP_interpolated.csv")
    set_field(load, "output_file", WORK_DIR / "load" / "GDP_trend.png")
    set_field(load, "variable", "GDP")
    set_field(load, "group_column", "CityName")
    chart = Path(load.task())
    assert chart.exists() and chart.stat().st_size > 0, "Load page did not create a usable chart"

    window.close()
    app.quit()
    print(f"GUI smoke flow passed. Outputs: {WORK_DIR}")


if __name__ == "__main__":
    main()
