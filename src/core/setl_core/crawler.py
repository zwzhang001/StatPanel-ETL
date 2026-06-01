"""CNKI statistical database crawler integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

import pandas as pd

CNKI_INDICATOR_SEARCH_URL = (
    "https://szjk.cnki.net/dpi/search-center/#/data-easysearchReplace?"
    "dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1&system=1"
)

CNKI_STAT_SEARCH_URL = (
    "https://szjk.cnki.net/dpi/search-center/#/data-indexs?"
    "dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1&system=1"
)

OUTPUT_COLUMNS = ["No", "Region", "Year", "Index", "Value", "Unit"]
CNKI_ORIGINAL_TABLE_COLUMNS = ["\u5e8f\u53f7", "\u65f6\u95f4", "\u5730\u533a", "\u6307\u6807\u540d\u79f0", "\u6570\u503c", "\u5355\u4f4d", "\u6570\u636e\u6765\u6e90"]
CITY_FIELDS_KEY = "_city_fields"


@dataclass
class CrawlerConfig:
    keyword: str
    city_file: str | Path
    start_year: int
    end_year: int
    output_dir: str | Path = "crawler_output"
    login_url: str = CNKI_INDICATOR_SEARCH_URL
    search_url: str = CNKI_STAT_SEARCH_URL
    selected_indexes: list[object] | None = None
    wait_seconds: float = 5.0
    login_wait_seconds: int = 120
    resume: bool = True
    headless: bool = False
    output_encoding: str = "gb18030"
    browser: str = "firefox"
    browser_profile_dir: str | Path | None = None


@dataclass
class CrawlerResult:
    downloaded_files: list[Path]
    failed_items: list[dict[str, str]]
    log_file: Path | None = None


def _dimension_id_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return (query.get("dimensionId") or ["595b3bf46c0b44469ae0d38f531cd789"])[0]


def _api_base_from_url(url: str) -> str:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://szjk.cnki.net"
    return origin.rstrip("/") + "/numerical-db-building"


def _clean_html_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return " ".join(text.replace("&nbsp;", " ").split())


def _indicator_display_name(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or item.get("label") or item.get("indexName") or item.get("query_name") or "").strip()
    if isinstance(item, str):
        text = item.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return _indicator_display_name(parsed)
            except json.JSONDecodeError:
                pass
        return text
    return str(item or "").strip()


def _indicator_query_name(item: object) -> str:
    if isinstance(item, dict):
        query_name = str(item.get("query_name") or item.get("indexName") or "").strip()
        if query_name:
            return query_name
    return indicator_search_keyword(_indicator_display_name(item))


def _indicator_code(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("code") or item.get("id") or item.get("indexId") or "").strip()
    if isinstance(item, str) and item.strip().startswith("{"):
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return _indicator_code(parsed)
        except json.JSONDecodeError:
            pass
    return ""


def _city_name_and_no(city: object) -> tuple[str, str]:
    if isinstance(city, dict):
        return str(city.get("name", "")).strip(), str(city.get("no", "")).strip()
    return str(city).strip(), ""


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def validate_crawler_config(config: CrawlerConfig) -> None:
    if not config.keyword:
        raise ValueError("keyword is required")
    if not config.city_file:
        raise ValueError("city_file is required")
    if not Path(config.city_file).exists():
        raise ValueError(f"city_file does not exist: {config.city_file}")
    if config.start_year <= 0 or config.end_year <= 0:
        raise ValueError("start_year and end_year must be positive")
    if config.start_year > config.end_year:
        raise ValueError("start_year must be less than or equal to end_year")
    if not config.search_url:
        raise ValueError("search_url is required")
    if config.wait_seconds <= 0:
        raise ValueError("wait_seconds must be positive")
    if config.login_wait_seconds < 0:
        raise ValueError("login_wait_seconds must be zero or positive")


def build_output_path(output_dir: str | Path, keyword: str, city_no: str) -> Path:
    safe_keyword = re.sub(r'[\\/:*?"<>|]+', "_", keyword).strip() or "cnki"
    return Path(output_dir) / f"{safe_keyword}_{city_no}.csv"


def read_city_rows(city_file: str | Path) -> list[dict[str, Any]]:
    df = _read_csv_with_fallback(city_file).fillna("")
    name_column = _first_existing_column(df.columns, ["CityName", "name", "Region", "region", "城市", "地区"])
    no_column = _first_existing_column(df.columns, ["no", "No", "code", "Code", "城市代码", "地区代码"])
    if not name_column:
        raise ValueError("city_file must include a city name column such as CityName or name")

    rows: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        name = str(row[name_column]).strip()
        if not name:
            continue
        city_no = str(row[no_column]).strip() if no_column else str(index + 1)
        city_fields = {str(column): str(row[column]).strip() for column in df.columns}
        rows.append({"no": city_no, "name": name, CITY_FIELDS_KEY: city_fields})
    if not rows:
        raise ValueError("city_file does not contain any usable city rows")
    return rows


def search_cnki_indicators(
    config: CrawlerConfig,
    *,
    progress: Callable[[str], None] | None = None,
    driver_factory: Callable[[CrawlerConfig], object] | None = None,
) -> list[str]:
    validate_crawler_config(config)
    driver = (driver_factory or _create_selenium_driver)(config)
    try:
        if progress:
            progress(f"Opening CNKI indicator search page: {config.login_url}")
        driver.get(config.login_url)
        _wait_for_manual_login(config.login_wait_seconds, progress or (lambda message: None))
        _wait_for_document_ready(driver, config.wait_seconds * 3)
        if _is_szjk_search_url(config.login_url):
            indicators = _search_cnki_indicator_candidates_api(driver, config, progress)
            if indicators:
                if progress:
                    progress(f"Matched indicators: {len(indicators)}")
                return indicators
        if _is_szjk_indexs_url(config.login_url):
            city_rows = read_city_rows(config.city_file)
            first_city = city_rows[0]["name"]
            if progress:
                progress(f"Searching indicator candidates on CNKI data-indexs page using city: {first_city}")
            return _search_szjk_indexs_indicator_candidates(driver, config, first_city, progress)
        _open_szjk_data_analysis(driver)
        if not _open_szjk_left_tab(driver, "选择指标"):
            time.sleep(min(max(config.wait_seconds, 1), 3))
            _open_szjk_data_analysis(driver)
            _open_szjk_left_tab(driver, "选择指标")
        if not _retry_bool(lambda: _search_szjk_indicator_tree(driver, indicator_search_keyword(config.keyword)), config.wait_seconds * 3):
            raise RuntimeError("CNKI indicator panel search box was not detected. Please confirm the left '选择指标' tab is available.")
        time.sleep(min(max(config.wait_seconds, 1), 5))
        indicators = _read_indicator_candidates_from_driver(driver, config.keyword)
        if not indicators:
            indicators = parse_indicator_candidates(driver.page_source, config.keyword)
        if progress:
            progress(f"Matched indicators: {len(indicators)}")
        return indicators
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def crawl_cnki(
    config: CrawlerConfig,
    *,
    progress: Callable[[str], None] | None = None,
    preview: Callable[[dict[str, object]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    driver_factory: Callable[[CrawlerConfig], object] | None = None,
) -> CrawlerResult:
    validate_crawler_config(config)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "crawler.log"
    downloaded_files: list[Path] = []
    failed_items: list[dict[str, str]] = []

    def log(message: str) -> None:
        if progress:
            progress(message)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    selected_indexes = [item for item in (config.selected_indexes or [config.keyword]) if _indicator_display_name(item)]
    cities = read_city_rows(config.city_file)
    driver = (driver_factory or _create_selenium_driver)(config)
    try:
        log(f"Opening CNKI login page: {config.login_url}")
        driver.get(config.login_url)
        _wait_for_manual_login(config.login_wait_seconds, log)
        for city in cities:
            if cancel_requested and cancel_requested():
                log("Crawler stopped by user.")
                break
            outpath = build_output_path(output_dir, config.keyword, city["no"])
            if config.resume and outpath.exists():
                log(f"Skip existing file: {outpath}")
                downloaded_files.append(outpath)
                if preview:
                    preview({"path": outpath, "city": city["name"], "city_no": city["no"], "row_count": None, "skipped": True})
                continue

            try:
                normalized_rows: list[dict[str, str]] = []
                for exact_index in selected_indexes:
                    if cancel_requested and cancel_requested():
                        log("Crawler stopped by user.")
                        break
                    exact_config = replace(config, selected_indexes=[exact_index])
                    raw_rows = _fetch_city_rows(driver, exact_config, city["name"], log, city_no=city["no"])
                    if _is_szjk_indexs_url(config.search_url):
                        normalized_rows.extend(_append_city_file_fields(raw_rows, city))
                    else:
                        normalized_rows.extend(normalize_crawler_rows(raw_rows, city=city, exact_index=_indicator_display_name(exact_index)))
                if cancel_requested and cancel_requested() and not normalized_rows:
                    break

                if _is_szjk_indexs_url(config.search_url):
                    frame = pd.DataFrame(normalized_rows, columns=_cnki_indexs_output_columns(city))
                else:
                    frame = pd.DataFrame(normalized_rows, columns=OUTPUT_COLUMNS)
                frame.to_csv(
                    outpath,
                    index=False,
                    encoding=config.output_encoding,
                )
                downloaded_files.append(outpath)
                if preview:
                    preview({"path": outpath, "city": city["name"], "city_no": city["no"], "row_count": len(normalized_rows), "skipped": False})
                log(f"Saved {len(normalized_rows)} rows: {outpath}")
            except Exception as exc:
                error = str(exc)
                if _is_browser_session_lost(error):
                    error = (
                        "Browser session was lost. Restart the crawler after checking Firefox, geckodriver, "
                        f"and CNKI login state. Original error: {exc}"
                    )
                    failed_items.append({"city": city["name"], "no": city["no"], "error": error})
                    log(f"Failed {city['name']} ({city['no']}): {error}")
                    break
                failed_items.append({"city": city["name"], "no": city["no"], "error": error})
                log(f"Failed {city['name']} ({city['no']}): {exc}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return CrawlerResult(downloaded_files=downloaded_files, failed_items=failed_items, log_file=log_file)


def _city_output_fields(city: dict[str, Any]) -> dict[str, str]:
    fields = city.get(CITY_FIELDS_KEY) if isinstance(city, dict) else None
    return dict(fields) if isinstance(fields, dict) else {}


def _cnki_indexs_output_columns(city: dict[str, Any]) -> list[str]:
    city_columns = [column for column in _city_output_fields(city) if column not in CNKI_ORIGINAL_TABLE_COLUMNS]
    return CNKI_ORIGINAL_TABLE_COLUMNS + city_columns


def _append_city_file_fields(rows: list[dict[str, str]], city: dict[str, Any]) -> list[dict[str, str]]:
    city_fields = _city_output_fields(city)
    enriched: list[dict[str, str]] = []
    for row in rows:
        output_row = {column: str(row.get(column, "")) for column in CNKI_ORIGINAL_TABLE_COLUMNS}
        for column, value in city_fields.items():
            if column not in output_row:
                output_row[column] = value
        enriched.append(output_row)
    return enriched


def normalize_crawler_rows(rows: list[dict[str, str]], *, city: dict[str, str], exact_index: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    unit = extract_unit_from_index(exact_index)
    for row in rows:
        normalized.append(
            {
                "No": str(city["no"]),
                "Region": str(city["name"]),
                "Year": str(row.get("Year", "")),
                "Index": exact_index,
                "Value": str(row.get("Value", "")),
                "Unit": unit or str(row.get("Unit", "")),
            }
        )
    return normalized


def _candidate_names_from_rows(rows: list[dict[str, str]]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for row in rows:
        index = str(row.get("Index", "")).strip()
        unit = str(row.get("Unit", "")).strip()
        if not index:
            continue
        candidate = index
        if unit and not re.search(rf"[（(]\s*{re.escape(unit)}\s*[）)]\s*$", index):
            candidate = f"{index}({unit})"
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def extract_unit_from_index(index_name: str) -> str:
    matches = re.findall(r"[（(]([^（）()]+)[）)]", index_name)
    return matches[-1].strip() if matches else ""


def indicator_search_keyword(index_name: str) -> str:
    keyword = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", index_name).strip()
    if re.search(r"[\u4e00-\u9fff]", keyword):
        keyword = re.sub(r"\s+", "", keyword)
    else:
        keyword = re.sub(r"\s+", " ", keyword)
    return keyword or index_name.strip()


def parse_indicator_candidates(html: str, keyword: str) -> list[str]:
    parser = _TextParser()
    parser.feed(html)
    seen: set[str] = set()
    candidates: list[str] = []
    for text in parser.parts:
        if keyword and keyword not in text:
            continue
        for candidate in _split_indicator_text(text, keyword):
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates


def _read_indicator_candidates_from_driver(driver: object, keyword: str) -> list[str]:
    script = r"""
    // INDICATOR_CANDIDATE_EXTRACTOR
    const keyword = arguments[0];
    const compactKeyword = (keyword || '').replace(/\s+/g, '');
    const nodes = Array.from(document.querySelectorAll(
      'label, li, .el-tree-node__content, .el-checkbox, [role="treeitem"], .el-tooltip, [title], [aria-label]'
    ));
    const seen = new Set();
    const results = [];
    function normalize(text) {
      return (text || '').replace(/\s+/g, ' ').trim();
    }
    function pieces(node) {
      const values = [
        node.getAttribute?.('title'),
        node.getAttribute?.('aria-label'),
        node.dataset?.title,
        node.dataset?.name,
        node.innerText,
        node.textContent
      ].map(normalize).filter(Boolean);
      const label = node.closest?.('label, li, .el-tree-node, .el-checkbox, [role="treeitem"]');
      if (label && label !== node) {
        values.push(
          normalize(label.getAttribute?.('title')),
          normalize(label.getAttribute?.('aria-label')),
          normalize(label.innerText),
          normalize(label.textContent)
        );
      }
      return values;
    }
    for (const node of nodes) {
      for (const text of pieces(node)) {
        const compactText = text.replace(/\s+/g, '');
        if (!text || (keyword && !text.includes(keyword) && !compactText.includes(compactKeyword))) continue;
        if (text.length > 160) continue;
        if (seen.has(text)) continue;
        seen.add(text);
        results.push(text);
      }
    }
    return results;
    """
    try:
        values = driver.execute_script(script, keyword)
    except Exception:
        return []
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def parse_cnki_rows(html: str) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    rows: list[dict[str, str]] = []
    for cells in parser.rows:
        if len(cells) < 9:
            continue
        if cells[1].lower() in {"no", "序号"} or cells[2].lower() in {"year", "年份"}:
            continue
        rows.append(
            {
                "No": cells[1],
                "Year": cells[2],
                "Region": cells[3],
                "Index": cells[4],
                "Value": cells[5],
                "Unit": cells[6],
                "Source": cells[7],
                "PageNumber": cells[8],
            }
        )
    return rows


def parse_szjk_rows(html: str, city_name: str, keyword: str) -> list[dict[str, str]]:
    if "\\u" in html:
        try:
            html = html.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    parser = _TableParser()
    parser.feed(html)
    rows: list[dict[str, str]] = []
    data_indexs_header_tokens = {"\u65f6\u95f4", "\u5730\u533a", "\u6307\u6807\u540d\u79f0", "\u6570\u503c", "\u5355\u4f4d"}
    for idx, cells in enumerate(parser.rows):
        if len(data_indexs_header_tokens.intersection(set(cells))) < 3:
            continue
        headers = cells
        for data_cells in parser.rows[idx + 1:]:
            if len(data_cells) < 2:
                continue
            if len(data_indexs_header_tokens.intersection(set(data_cells))) >= 3:
                break
            row = _map_szjk_row(headers, data_cells, city_name, keyword)
            if row:
                rows.append(row)
        if rows:
            return rows
    for headers, cells in _iter_data_rows(parser.rows):
        wide_rows = _map_szjk_wide_rows(headers, cells, city_name, keyword)
        if wide_rows:
            rows.extend(wide_rows)
            continue
        row = _map_szjk_row(headers, cells, city_name, keyword)
        if row:
            rows.append(row)
    return rows


def _fetch_city_rows(driver: object, config: CrawlerConfig, city_name: str, log: Callable[[str], None], city_no: str = "") -> list[dict[str, str]]:
    if _is_szjk_search_url(config.search_url):
        return _fetch_szjk_rows(driver, config, {"name": city_name, "no": city_no} if city_no else city_name, log)
    return _fetch_legacy_rows(driver, config, city_name, log)


def _fetch_szjk_indexs_rows(driver: object, config: CrawlerConfig, city: object, log: Callable[[str], None]) -> list[dict[str, str]]:
    city_name, city_no = _city_name_and_no(city)
    exact_index = (config.selected_indexes or [config.keyword])[0]
    indicator = _resolve_cnki_indicator_selection(driver, config, exact_index, log)
    query_index = _indicator_query_name(indicator)
    log(f"Setting CNKI data-indexs page controls: index={indicator.get('name') or query_index}; region={city_name}; dates={config.start_year}-{config.end_year}")
    last_state: object = {}
    last_records: list[dict[str, object]] = []
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        driver.get(config.search_url)
        _wait_for_document_ready(driver, config.wait_seconds * 3)
        state = _submit_szjk_indexs_query_via_vue(
            driver,
            indicator,
            city_name,
            city_no,
            config.start_year,
            config.end_year,
            timeout=max(config.wait_seconds * 6, 5),
        )
        last_state = state
        if not state.get("ready"):
            if attempt < max_attempts:
                log(
                    "CNKI data-indexs page controls were not ready "
                    f"for {city_name} / {query_index}; retrying. "
                    f"State: {state.get('summary', state)}"
                )
                continue
            raise RuntimeError(f"CNKI data-indexs page controls did not match requested query: {state.get('summary', state)}")
        log(f"CNKI data-indexs page query submitted: {state.get('summary', state)}")
        records = state.get("rows", [])
        if not isinstance(records, list):
            records = []
        last_records = [record for record in records if isinstance(record, dict)]
        filtered_records = _filter_cnki_indexs_records(last_records, city_name, city_no, query_index, config.start_year, config.end_year)
        if last_records and not filtered_records and attempt < max_attempts:
            log(
                "Detected stale data-indexs rows after query "
                f"for {city_name} / {query_index}; retrying once. "
                f"Current row sample: {_summarize_cnki_records(last_records)}"
            )
            continue
        rows = _records_to_original_table_rows(filtered_records)
        if last_records:
            keys = sorted({str(key) for record in last_records for key in record.keys()})
            log(f"CNKI tableData fields: {keys}")
        if not rows:
            log(f"No matching data-indexs table rows returned for {city_name} / {query_index} / {config.start_year}-{config.end_year}. Raw state: {last_state.get('summary', last_state) if isinstance(last_state, dict) else last_state}")
        return rows
    return _records_to_original_table_rows(_filter_cnki_indexs_records(last_records, city_name, city_no, query_index, config.start_year, config.end_year))


def _search_szjk_indexs_indicator_candidates(
    driver: object,
    config: CrawlerConfig,
    city_name: str,
    progress: Callable[[str], None] | None,
) -> list[str]:
    query_keyword = indicator_search_keyword(config.keyword)
    if not _retry_bool(lambda: _fill_szjk_indexs_indicator_name(driver, query_keyword), config.wait_seconds * 3):
        raise RuntimeError(
            "CNKI data-indexs indicator-name field was not detected while searching indicator candidates. "
            "Please confirm the page has loaded and the current account can access the indicator search page."
        )
    if not _click_szjk_indexs_query(driver):
        if progress:
            progress("CNKI data-indexs query button was not detected; reading visible candidate text from the current page.")
    else:
        _wait_for_document_ready(driver, config.wait_seconds * 3)
        time.sleep(min(max(config.wait_seconds, 1), 5))
    rows = _filter_rows_by_year(parse_szjk_rows(driver.page_source, city_name, config.keyword), config.start_year, config.end_year)
    indicators = _candidate_names_from_rows(rows)
    if not indicators:
        indicators = _read_indicator_candidates_from_driver(driver, config.keyword)
    if not indicators:
        indicators = parse_indicator_candidates(driver.page_source, config.keyword)
    if progress:
        progress(f"Matched indicators: {len(indicators)}")
    return indicators


def _fetch_szjk_rows(driver: object, config: CrawlerConfig, city: object, log: Callable[[str], None]) -> list[dict[str, str]]:
    city_name, _city_no = _city_name_and_no(city)
    if _is_szjk_indexs_url(config.search_url):
        return _fetch_szjk_indexs_rows(driver, config, city, log)

    exact_index = (config.selected_indexes or [config.keyword])[0]
    exact_index_name = _indicator_display_name(exact_index)
    log(f"Searching {exact_index_name} for {city_name} on CNKI statistical search center")
    driver.get(config.search_url)
    _wait_for_document_ready(driver, config.wait_seconds * 3)
    _open_szjk_data_analysis(driver)
    _clear_szjk_selected_content(driver)

    if not _open_szjk_left_tab(driver, "地区选择"):
        log("CNKI region tab was not detected automatically; trying to select region on the current panel.")
    _search_szjk_region_tree(driver, city_name)
    time.sleep(min(max(config.wait_seconds / 2, 1), 3))
    if not _select_szjk_region(driver, city_name):
        raise RuntimeError(f"CNKI region checkbox was not detected on the region tree. City: {city_name}")
    _open_szjk_left_tab(driver, "选择指标")
    if not _search_szjk_indicator_tree(driver, indicator_search_keyword(exact_index_name)):
        raise RuntimeError("CNKI indicator panel search box was not detected after opening the left '选择指标' tab.")
    time.sleep(min(max(config.wait_seconds / 2, 1), 3))
    if _select_szjk_indicator(driver, exact_index_name):
        log(f"Exact indicator selected: {exact_index_name}")
    else:
        raise RuntimeError(
            "Exact indicator checkbox was not detected on the CNKI indicator tree. "
            f"Selected indicator: {exact_index_name}. Please rerun Search Indicators and choose the full indicator name with unit."
        )
    selection_state = _read_szjk_selected_content_state(driver, city_name, exact_index_name)
    if selection_state.get("visible") and not selection_state.get("ready"):
        log(
            "Selected content did not contain exactly the current city and exact indicator; "
            f"rebuilding selection. State: {selection_state.get('summary', '')}"
        )
        _clear_szjk_selected_content(driver)
        time.sleep(0.5)
        if not _open_szjk_left_tab(driver, "地区选择"):
            log("CNKI region tab was not detected automatically during selection rebuild.")
        _search_szjk_region_tree(driver, city_name)
        time.sleep(min(max(config.wait_seconds / 2, 1), 3))
        if not _select_szjk_region(driver, city_name):
            raise RuntimeError(f"CNKI region checkbox was not detected after rebuilding selection. City: {city_name}")
        _open_szjk_left_tab(driver, "选择指标")
        if not _search_szjk_indicator_tree(driver, indicator_search_keyword(exact_index_name)):
            raise RuntimeError("CNKI indicator panel search box was not detected during selection rebuild.")
        time.sleep(min(max(config.wait_seconds / 2, 1), 3))
        if not _select_szjk_indicator(driver, exact_index_name):
            raise RuntimeError(f"Exact indicator checkbox was not detected after rebuilding selection: {exact_index_name}")
        selection_state = _read_szjk_selected_content_state(driver, city_name, exact_index_name)
    if selection_state.get("visible") and not selection_state.get("has_index"):
        raise RuntimeError(f"CNKI selected indicator is still empty after checkbox click. Indicator: {exact_index_name}")
    if not _click_szjk_selected_content_query(driver):
        raise RuntimeError("CNKI selected-content query button was not detected after selecting one region and one indicator.")
    log(f"Initial region/index query submitted: {city_name} / {exact_index_name}")
    _wait_for_document_ready(driver, config.wait_seconds * 3)
    time.sleep(min(max(config.wait_seconds, 1), 3))

    time_range_applied = _fill_szjk_time_range(driver, config.start_year, config.end_year)
    if time_range_applied:
        log(f"Time range applied: {config.start_year}-{config.end_year}")
    else:
        raise RuntimeError(
            "CNKI time range picker was not applied. "
            f"Expected selected years: {config.start_year}-{config.end_year}. "
            "Please confirm the page exposes the '按时间范围选择' picker."
        )
    if not _click_szjk_time_query(driver):
        raise RuntimeError("CNKI time-range query button was not detected next to the '按时间范围选择' control.")
    log(f"Final time-range query submitted: {city_name} / {exact_index_name} / {config.start_year}-{config.end_year}")
    _wait_for_document_ready(driver, config.wait_seconds * 3)
    time.sleep(min(max(config.wait_seconds, 1), 5))
    if not _wait_for_szjk_year_headers(driver, config.start_year, config.end_year, config.wait_seconds * 2):
        log(f"Requested year headers were not detected after query; retrying time range: {config.start_year}-{config.end_year}")
        if not _fill_szjk_time_range(driver, config.start_year, config.end_year) or not _click_szjk_time_query(driver):
            raise RuntimeError(f"CNKI time range retry failed: {config.start_year}-{config.end_year}")
        _wait_for_document_ready(driver, config.wait_seconds * 3)
        time.sleep(min(max(config.wait_seconds, 1), 5))
    if not _wait_for_szjk_year_headers(driver, config.start_year, config.end_year, 1):
        raise RuntimeError(
            "CNKI result table did not update to the requested year range. "
            f"Expected table headers containing {config.start_year}年 and {config.end_year}年."
        )
    rows = _filter_rows_by_year(parse_szjk_rows(driver.page_source, city_name, exact_index_name), config.start_year, config.end_year)
    if not rows:
        log(f"No rows parsed for {city_name} / {exact_index_name}; the page may need selector tuning.")
    return rows


def _fetch_legacy_rows(driver: object, config: CrawlerConfig, city_name: str, log: Callable[[str], None]) -> list[dict[str, str]]:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    log(f"Searching {config.keyword} for {city_name}")
    driver.get(config.search_url)
    _wait_until_overlay_hidden(driver, config.wait_seconds)

    _clear_and_type(driver.find_element(By.NAME, "IndicateName"), config.keyword)
    _clear_and_type(driver.find_element(By.NAME, "IndicateRegion"), city_name)
    Select(driver.find_element(By.NAME, "StartYear")).select_by_visible_text(str(config.start_year))
    Select(driver.find_element(By.NAME, "EndYear")).select_by_visible_text(str(config.end_year))
    driver.find_element(By.ID, "AdvancedSearch").click()
    _wait_until_overlay_hidden(driver, config.wait_seconds * 2)

    page_count = _read_page_count(driver.page_source)
    rows: list[dict[str, str]] = []
    for page_no in range(page_count):
        rows.extend(parse_cnki_rows(driver.page_source))
        if page_no < page_count - 1:
            driver.find_element(By.ID, "NextPage").click()
            _wait_until_overlay_hidden(driver, config.wait_seconds)
    return rows


def _iter_data_rows(rows: list[list[str]]) -> Iterable[tuple[list[str], list[str]]]:
    headers: list[str] = []
    for cells in rows:
        if len(cells) < 2:
            continue
        if _looks_like_header_row(cells):
            headers = cells
            continue
        yield headers, cells


def _looks_like_header_row(cells: list[str]) -> bool:
    tokens = ["Region", "City", "Area", "Year", "Index", "Value", "Unit", "地区", "城市", "区域", "年份", "年度", "指标", "数值", "单位"]
    matching_cells = 0
    for cell in cells:
        stripped = cell.strip()
        if any(stripped == token or token in stripped for token in tokens):
            matching_cells += 1
        elif re.search(r"(?:19|20)\d{2}\s*年?$", stripped):
            matching_cells += 1
    return matching_cells >= 2


def _map_szjk_row(headers: list[str], cells: list[str], city_name: str, keyword: str) -> dict[str, str] | None:
    if len(cells) < 2:
        return None
    year = _cell_by_header(headers, cells, ["Year", "年份", "年度", "时间"]) or _first_year_cell(cells)
    index = _cell_by_header(headers, cells, ["Index", "指标", "项目", "数据项"]) or _first_matching_cell(cells, keyword) or keyword
    value = _cell_by_header(headers, cells, ["Value", "数值", "数据", "值"]) or _first_numeric_cell(cells)
    unit = _cell_by_header(headers, cells, ["Unit", "单位"]) or extract_unit_from_index(index)
    region = _cell_by_header(headers, cells, ["Region", "City", "Area", "地区", "城市", "区域"]) or _first_matching_cell(cells, city_name) or city_name
    if not value:
        return None
    return {"No": "", "Year": year or "", "Region": region, "Index": index, "Value": value, "Unit": unit}


def _map_szjk_wide_rows(headers: list[str], cells: list[str], city_name: str, keyword: str) -> list[dict[str, str]]:
    year_columns: list[tuple[int, str]] = []
    for idx, header in enumerate(headers):
        match = re.search(r"((?:19|20)\d{2})\s*年?", header)
        if match and idx < len(cells):
            year_columns.append((idx, match.group(1)))
    if not year_columns:
        return []

    index = _cell_by_header(headers, cells, ["指标名称", "指标", "Index"]) or _first_matching_cell(cells, keyword) or keyword
    region = _cell_by_header(headers, cells, ["地区", "城市", "Region", "City"]) or _first_matching_cell(cells, city_name) or city_name
    unit = extract_unit_from_index(index) or _cell_by_header(headers, cells, ["单位", "Unit"])
    rows: list[dict[str, str]] = []
    for idx, year in year_columns:
        value = cells[idx].strip()
        if not value:
            continue
        rows.append({"No": "", "Year": year, "Region": region, "Index": index, "Value": value, "Unit": unit})
    return rows


def _looks_like_header_row(cells: list[str]) -> bool:
    tokens = [
        "Region", "City", "Area", "Year", "Index", "Value", "Unit",
        "\u5e8f\u53f7", "\u65f6\u95f4", "\u5730\u533a", "\u6307\u6807\u540d\u79f0",
        "\u6570\u503c", "\u5355\u4f4d", "\u6570\u636e\u6765\u6e90",
    ]
    matching_cells = 0
    for cell in cells:
        stripped = cell.strip()
        if any(stripped == token or token in stripped for token in tokens):
            matching_cells += 1
        elif re.search(r"(?:19|20)\d{2}\s*(?:\u5e74)?$", stripped):
            matching_cells += 1
    return matching_cells >= 2


def _map_szjk_row(headers: list[str], cells: list[str], city_name: str, keyword: str) -> dict[str, str] | None:
    if len(cells) < 2:
        return None
    year = _cell_by_header(headers, cells, ["Year", "\u65f6\u95f4"]) or _first_year_cell(cells)
    index = _cell_by_header(headers, cells, ["Index", "\u6307\u6807\u540d\u79f0"]) or _first_matching_cell(cells, keyword) or keyword
    value = _cell_by_header(headers, cells, ["Value", "\u6570\u503c"]) or _first_numeric_cell(cells)
    unit = _cell_by_header(headers, cells, ["Unit", "\u5355\u4f4d"]) or extract_unit_from_index(index)
    region = _cell_by_header(headers, cells, ["Region", "City", "Area", "\u5730\u533a"]) or _first_matching_cell(cells, city_name) or city_name
    if not value:
        return None
    year_match = re.search(r"(?:19|20)\d{2}", year or "")
    if year_match:
        year = year_match.group(0)
    return {"No": "", "Year": year or "", "Region": region, "Index": index, "Value": value, "Unit": unit}


def _split_indicator_text(text: str, keyword: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(r"[\n\r;；]+", cleaned)
    results: list[str] = []
    for part in parts:
        part = part.strip(" -|")
        if keyword in part and len(part) <= 120:
            results.append(part)
    return results


def _read_csv_with_fallback(path: str | Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gb2312"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error or ValueError(f"Unable to read CSV: {path}")


def _first_existing_column(columns: Iterable[str], candidates: list[str]) -> str | None:
    lower_map = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return None


def _cell_by_header(headers: list[str], cells: list[str], candidates: list[str]) -> str:
    for idx, header in enumerate(headers):
        if idx < len(cells) and any(candidate in header for candidate in candidates):
            return cells[idx]
    return ""


def _first_matching_cell(cells: list[str], needle: str) -> str:
    if not needle:
        return ""
    for cell in cells:
        if needle in cell:
            return cell
    return ""


def _first_year_cell(cells: list[str]) -> str:
    for cell in cells:
        match = re.search(r"(19|20)\d{2}", cell)
        if match:
            return match.group(0)
    return ""


def _first_numeric_cell(cells: list[str]) -> str:
    for cell in cells:
        compact = cell.replace(",", "").strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", compact):
            return cell.strip()
    return ""


def _filter_rows_by_year(rows: list[dict[str, str]], start_year: int, end_year: int) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for row in rows:
        match = re.search(r"(?:19|20)\d{2}", str(row.get("Year", "")))
        if not match:
            continue
        year = int(match.group(0))
        if start_year <= year <= end_year:
            next_row = dict(row)
            next_row["Year"] = str(year)
            filtered.append(next_row)
    return filtered


def _filter_rows_by_request(rows: list[dict[str, str]], city_name: str, keyword: str, start_year: int, end_year: int) -> list[dict[str, str]]:
    year_filtered = _filter_rows_by_year(rows, start_year, end_year)
    compact_city = re.sub(r"\s+", "", city_name)
    city_names = {compact_city}
    if compact_city and not re.search(r"[市县区州盟]$", compact_city):
        city_names.add(f"{compact_city}市")
    if compact_city.endswith("市"):
        city_names.add(compact_city[:-1])
    compact_keyword = re.sub(r"\s+", "", keyword)
    filtered: list[dict[str, str]] = []
    for row in year_filtered:
        row_region = re.sub(r"\s+", "", str(row.get("Region", "")))
        row_index = re.sub(r"\s+", "", str(row.get("Index", "")))
        region_matches = not compact_city or any(name and (row_region == name or row_region.endswith(name) or name in row_region) for name in city_names)
        index_matches = not compact_keyword or row_index == compact_keyword or compact_keyword in row_index
        if region_matches and index_matches:
            filtered.append(row)
    return filtered


def _candidate_from_api_item(item: object) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    raw_name = _clean_html_text(item.get("name") or item.get("indexName") or item.get("title") or item.get("label") or "")
    if not raw_name:
        return None
    unit = _clean_html_text(item.get("unit") or item.get("unitName") or "")
    display_name = raw_name
    if unit and not raw_name.endswith(f"({unit})") and not raw_name.endswith(f"\uff08{unit}\uff09"):
        display_name = f"{raw_name}({unit})"
    code = str(item.get("code") or item.get("id") or item.get("indexId") or "").strip()
    candidate = {
        "name": display_name,
        "query_name": raw_name,
        "code": code,
    }
    if item.get("id") is not None:
        candidate["id"] = str(item.get("id"))
    return candidate


def _search_cnki_indicator_candidates_api(
    driver: object,
    config: CrawlerConfig,
    progress: Callable[[str], None] | None,
) -> list[dict[str, str]]:
    keyword = indicator_search_keyword(config.keyword)
    if progress:
        progress(f"Searching indicator candidates through CNKI API: {keyword}")
    try:
        data = _cnki_api_request_json(
            driver,
            config.login_url,
            "/select/fuzzyQueryFinalIndexes",
            {"dimensionId": _dimension_id_from_url(config.login_url), "fuzzyName": keyword},
            method="get",
            json_payload=False,
        )
    except Exception as exc:
        if progress:
            progress(f"CNKI indicator API search failed; falling back to page parsing. Error: {exc}")
        return []
    result = data.get("result", []) if isinstance(data, dict) else []
    if isinstance(result, dict):
        result = result.get("records", []) or result.get("list", []) or result.get("data", [])
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in result if isinstance(result, list) else []:
        candidate = _candidate_from_api_item(item)
        if not candidate:
            continue
        key = candidate.get("code") or candidate["name"]
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _resolve_cnki_indicator_selection(
    driver: object,
    config: CrawlerConfig,
    selection: object,
    log: Callable[[str], None],
) -> dict[str, str]:
    name = _indicator_display_name(selection)
    query_name = _indicator_query_name(selection)
    code = _indicator_code(selection)
    if code:
        return {"name": name, "query_name": query_name, "code": code}

    data = _cnki_api_request_json(
        driver,
        config.search_url,
        "/select/fuzzyQueryFinalIndexes",
        {"dimensionId": _dimension_id_from_url(config.search_url), "fuzzyName": query_name},
        method="get",
        json_payload=False,
    )
    result = data.get("result", []) if isinstance(data, dict) else []
    if isinstance(result, dict):
        result = result.get("records", []) or result.get("list", []) or result.get("data", [])
    candidates = [_candidate_from_api_item(item) for item in result if isinstance(item, dict)]
    candidates = [item for item in candidates if item]
    compact_query = re.sub(r"\s+", "", query_name)
    compact_name = re.sub(r"\s+", "", name)
    for candidate in candidates:
        candidate_names = {
            re.sub(r"\s+", "", candidate.get("query_name", "")),
            re.sub(r"\s+", "", candidate.get("name", "")),
        }
        if compact_query in candidate_names or compact_name in candidate_names:
            if candidate.get("code"):
                log(f"Resolved indicator code: {candidate.get('name')} -> {candidate.get('code')}")
                return candidate
    for candidate in candidates:
        if candidate.get("code"):
            log(f"Resolved indicator code by fuzzy fallback: {candidate.get('name')} -> {candidate.get('code')}")
            return candidate
    raise RuntimeError(f"CNKI indicator code was not resolved for selected indicator: {name}")


def _find_region_node(nodes: object, city_name: str, city_no: str = "") -> dict[str, str] | None:
    if isinstance(nodes, dict):
        for key in ("sysRegionList", "records", "list", "children", "children2"):
            value = nodes.get(key)
            if value:
                found = _find_region_node(value, city_name, city_no)
                if found:
                    return found
    compact_city = re.sub(r"\s+", "", city_name)
    city_aliases = {compact_city}
    if compact_city and not re.search(r"[\u5e02\u53bf\u533a\u5dde\u76df]$", compact_city):
        city_aliases.add(f"{compact_city}\u5e02")
    if compact_city.endswith("\u5e02"):
        city_aliases.add(compact_city[:-1])
    stack = list(nodes if isinstance(nodes, list) else [nodes])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        code = str(node.get("regionCode") or node.get("code") or node.get("value") or "").strip()
        name = re.sub(r"\s+", "", str(node.get("regionName") or node.get("name") or node.get("label") or ""))
        if (city_no and code == city_no) or any(alias and (name == alias or name.endswith(alias)) for alias in city_aliases):
            return {
                "regionName": str(node.get("regionName") or node.get("name") or node.get("label") or city_name),
                "regionCode": code or city_no,
                "type": str(node.get("type") or node.get("areaType") or "city"),
            }
        for child_key in ("children", "children2"):
            children = node.get(child_key)
            if isinstance(children, list):
                stack.extend(children)
    return None


def _resolve_szjk_region(
    driver: object,
    config: CrawlerConfig,
    city_name: str,
    city_no: str,
    indicator: dict[str, str],
    log: Callable[[str], None],
) -> dict[str, str]:
    payload = {
        "dimensionId": _dimension_id_from_url(config.search_url),
        "indexIds": [indicator["code"]],
        "timeFrequency": "year",
        "areaCodes": [],
    }
    try:
        data = _cnki_api_request_json(
            driver,
            config.search_url,
            "/select/listRegionConfEffective",
            payload,
            method="post",
            json_payload=True,
        )
        result = data.get("result", []) if isinstance(data, dict) else []
        node = _find_region_node(result, city_name, city_no)
        if node:
            log(f"Resolved region code: {city_name} -> {node['regionCode']} ({node.get('type', '')})")
            return node
    except Exception as exc:
        log(f"CNKI region API lookup failed; falling back to city file code if available. Error: {exc}")
    if city_no:
        log(f"Using city-file region code fallback: {city_name} -> {city_no}; type=city")
        return {"regionName": city_name, "regionCode": city_no, "type": "city"}
    raise RuntimeError(f"CNKI region code was not resolved for city: {city_name}")


def _submit_szjk_indexs_query_via_vue(
    driver: object,
    indicator: dict[str, str],
    city_name: str,
    city_no: str,
    start_year: int,
    end_year: int,
    *,
    timeout: float,
) -> dict[str, object]:
    script = r"""
    const indicator = arguments[0] || {};
    const cityName = String(arguments[1] || '');
    const cityNo = String(arguments[2] || '');
    const startYear = String(arguments[3]);
    const endYear = String(arguments[4]);
    const timeoutMs = Math.max(Number(arguments[5] || 5) * 1000, 1000);
    const done = arguments[arguments.length - 1];

    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const compact = value => String(value || '').replace(/\s+/g, '');

    function findIndexPageVm() {
      const seen = new Set();
      const roots = Array.from(document.querySelectorAll('*'))
        .map(el => el.__vue__)
        .filter(Boolean);
      function visit(vm) {
        if (!vm || seen.has(vm)) return null;
        seen.add(vm);
        if (vm.searchQuery && typeof vm.handleSearch === 'function' && vm.$refs && vm.$refs.chooseArea && vm.$refs.timeChoose) {
          return vm;
        }
        const children = vm.$children || [];
        for (const child of children) {
          const found = visit(child);
          if (found) return found;
        }
        return null;
      }
      for (const root of roots) {
        const found = visit(root);
        if (found) return found;
      }
      return null;
    }

    function nextTick(vm) {
      return new Promise(resolve => vm.$nextTick ? vm.$nextTick(resolve) : resolve());
    }

    async function waitFor(predicate, label) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() <= deadline) {
        try {
          const value = predicate();
          if (value) return value;
        } catch (e) {}
        await sleep(250);
      }
      throw new Error(`Timed out waiting for ${label}`);
    }

    function childNodes(node) {
      const children = [];
      if (Array.isArray(node?.children)) children.push(...node.children);
      if (Array.isArray(node?.children2)) children.push(...node.children2);
      return children;
    }

    function findRegionPath(nodes, cityName, cityNo) {
      const compactCity = compact(cityName);
      const aliases = new Set([compactCity]);
      if (compactCity && !/[\u5e02\u53bf\u533a\u5dde\u76df]$/.test(compactCity)) aliases.add(`${compactCity}\u5e02`);
      if (compactCity.endsWith('\u5e02')) aliases.add(compactCity.slice(0, -1));
      const startNodes = Array.isArray(nodes) ? nodes : [];
      const stack = startNodes.map(node => ({node, path: [node]}));
      while (stack.length) {
        const {node, path} = stack.shift();
        const code = String(node?.regionCode || node?.code || node?.value || '');
        const name = compact(node?.regionName || node?.name || node?.label || '');
        if ((cityNo && code === cityNo) || Array.from(aliases).some(alias => alias && (name === alias || name.endsWith(alias)))) {
          return path;
        }
        for (const child of childNodes(node)) stack.push({node: child, path: path.concat([child])});
      }
      return null;
    }

    function cloneTableRows(vm) {
      const rows = Array.isArray(vm.tableData) ? vm.tableData : [];
      const columnKeys = (Array.isArray(vm.columns) ? vm.columns : [])
        .map(column => column && column.dataIndex)
        .filter(Boolean);
      const keys = Array.from(new Set([
        ...columnKeys,
        'id', 'date', 'area', 'indexName', 'value', 'unit', 'data_source', 'njnf',
        'time', 'year', 'region', 'regionName', 'name', 'index', 'dataValue', 'unitName'
      ]));
      return rows.map(row => {
        const plain = {};
        for (const key of keys) {
          try {
            const value = row?.[key];
            if (value !== undefined) plain[key] = value;
          } catch (e) {}
        }
        for (const key of Object.keys(row || {})) {
          if (!(key in plain) && key !== '__ob__') plain[key] = row[key];
        }
        return plain;
      });
    }

    async function forceSelectedIndex(vm, displayName, indexCode) {
      vm.indexName = displayName;
      vm.$set(vm.searchQuery, 'indexIds', [indexCode]);
      await nextTick(vm);
      await waitFor(
        () => Array.isArray(vm.searchQuery?.indexIds) && vm.searchQuery.indexIds.includes(indexCode),
        'indicator index id'
      );
    }

    async function forceSelectedRegion(vm, area, regionPath) {
      const last = regionPath[regionPath.length - 1] || {};
      const regionCode = String(last.regionCode || last.code || last.value || '');
      const regionType = last.type || last.areaType || 'xzqh';
      if (area) {
        area.radioValue = regionCode;
        area.value = regionCode;
      }
      vm.$set(vm.searchQuery, 'areaCodes', [regionCode]);
      vm.$set(vm.searchQuery, 'type', regionType);
      await nextTick(vm);
      await waitFor(
        () => Array.isArray(vm.searchQuery?.areaCodes) && vm.searchQuery.areaCodes.includes(regionCode),
        'region area code'
      );
      return {regionCode, regionType};
    }

    (async () => {
      try {
        const vm = await waitFor(findIndexPageVm, 'data-indexs Vue component');
        const indexName = String(indicator.query_name || indicator.name || '').replace(/\s+/g, '');
        const displayName = String(indicator.name || indicator.query_name || indexName);
        const indexCode = String(indicator.code || indicator.id || indicator.indexId || '');
        if (!indexCode) throw new Error(`Missing selected indicator code for ${displayName}`);

        if (typeof vm.treeSelect === 'function') {
          vm.treeSelect({name: displayName, code: indexCode, id: indexCode});
        } else {
          vm.indexName = displayName;
          vm.$set(vm.searchQuery, 'indexIds', [indexCode]);
        }
        await forceSelectedIndex(vm, displayName, indexCode);
        vm.$set(vm.searchQuery, 'timeFrequency', 'year');
        vm.$set(vm.searchQuery, 'timeFrequencies', ['\u5e74\u5ea6']);
        await nextTick(vm);

        const area = vm.$refs.chooseArea;
        if (!area) throw new Error('Missing chooseArea component');
        vm.tableData = [];
        vm.selectedRowKeys = [];
        vm.$set(vm.searchQuery, 'areaCodes', []);
        vm.$set(vm.searchQuery, 'type', undefined);
        area.radioValue = '';
        area.value = '';
        if (typeof area.setTree === 'function') area.setTree();
        await waitFor(() => Array.isArray(area.areaTreeData) && area.areaTreeData.length ? area.areaTreeData : null, 'region tree');
        const regionPath = findRegionPath(area.areaTreeData, cityName, cityNo);
        if (!regionPath) throw new Error(`Region not found in CNKI region tree: ${cityName} / ${cityNo}`);
        const regionCodes = regionPath.map(node => node.regionCode);
        if (typeof area.handleRadioChange === 'function') {
          area.handleRadioChange(regionCodes, regionPath);
        } else if (typeof vm.areaChange === 'function') {
          const last = regionPath[regionPath.length - 1];
          vm.areaChange({areaType: last.type, areaCodes: last.regionCode, areaList: regionPath});
        }
        await nextTick(vm);
        const selectedRegion = await forceSelectedRegion(vm, area, regionPath);
        await forceSelectedIndex(vm, displayName, indexCode);

        const timeChoose = vm.$refs.timeChoose;
        if (!timeChoose) throw new Error('Missing timeChoose component');
        if (typeof timeChoose.init === 'function') timeChoose.init('year');
        await nextTick(vm);
        if (typeof timeChoose.initVal === 'function') {
          timeChoose.initVal(`${startYear},${endYear}`, false);
        }
        timeChoose.frequency = 'year';
        timeChoose.startTime = {time1: startYear, time2: 1};
        timeChoose.endTime = {time1: endYear, time2: 1};
        if (typeof vm.timeChange === 'function') {
          vm.timeChange({dates: `${startYear},${endYear}`, timeType: 1});
        } else {
          vm.$set(vm.searchQuery, 'dates', [startYear, endYear]);
        }
        await nextTick(vm);
        await forceSelectedIndex(vm, displayName, indexCode);

        if (typeof vm.pageReset === 'function') vm.pageReset();
        vm.tableData = [];
        vm.selectedRowKeys = [];
        if (typeof vm.handleSearch !== 'function') throw new Error('Missing handleSearch method');
        const searchResult = vm.handleSearch('resetPage');
        if (searchResult && typeof searchResult.then === 'function') await searchResult;

        await waitFor(() => vm.loading === false && Array.isArray(vm.tableData), 'query completion');
        const query = vm.searchQuery || {};
        const rows = cloneTableRows(vm);
        const ready =
          Array.isArray(query.indexIds) && query.indexIds.includes(indexCode) &&
          Array.isArray(query.areaCodes) && query.areaCodes.includes(selectedRegion.regionCode) &&
          Array.isArray(query.dates) && query.dates.includes(startYear) && query.dates.includes(endYear) &&
          query.timeFrequency === 'year';
        done({
          ready,
          rows,
          summary: `indexIds=${JSON.stringify(query.indexIds || [])}; areaCodes=${JSON.stringify(query.areaCodes || [])}; type=${query.type}; dates=${JSON.stringify(query.dates || [])}; timeFrequency=${query.timeFrequency}; rows=${rows.length}`,
          query: {
            indexIds: query.indexIds || [],
            areaCodes: query.areaCodes || [],
            type: query.type,
            dates: query.dates || [],
            timeFrequency: query.timeFrequency,
            timeFrequencies: query.timeFrequencies || []
          }
        });
      } catch (error) {
        done({ready: false, rows: [], summary: String(error && error.message ? error.message : error)});
      }
    })();
    """
    try:
        state = driver.execute_async_script(
            script,
            indicator,
            city_name,
            city_no,
            start_year,
            end_year,
            timeout,
        )
    except Exception as exc:
        return {"ready": False, "rows": [], "summary": f"Vue query script failed: {exc}"}
    return state if isinstance(state, dict) else {"ready": False, "rows": [], "summary": "invalid Vue query state"}


def _fetch_szjk_field_json(driver: object, config: CrawlerConfig, log: Callable[[str], None]) -> dict[str, str]:
    try:
        data = _cnki_api_request_json(
            driver,
            config.search_url,
            "/select/getDynamicDimensions",
            {"dimensionId": _dimension_id_from_url(config.search_url)},
            method="get",
            json_payload=False,
        )
    except Exception as exc:
        log(f"CNKI dynamic-dimensions lookup failed; using empty field json. Error: {exc}")
        return {}
    result = data.get("result", []) if isinstance(data, dict) else []
    field_json = _build_szjk_field_json(result if isinstance(result, list) else [])
    log(f"Resolved dynamic field json keys: {list(field_json.keys())}")
    return field_json


def _build_szjk_field_json(dimensions: list[dict[str, object]]) -> dict[str, str]:
    fixed_search = {
        "\u6307\u6807\u540d\u79f0",
        "\u6307\u6807\u5730\u533a",
        "\u6307\u6807\u9891\u7387",
        "\u6307\u6807\u65f6\u95f4",
        "\u65f6\u95f4\u9891\u7387",
        "\u5e74",
        "\u5b63",
        "\u6708",
        "\u7701",
        "\u5e02",
        "\u533a\u53bf",
        "\u4e61\u9547",
        "\u6751",
        "\u5730\u533a",
    }
    field_json: dict[str, str] = {}
    for item in dimensions:
        description = str(item.get("description") or "").strip()
        flags = f"{item.get('jswdFlag') or ''},"
        if description and description not in fixed_search and "zbjs," in flags:
            field_json[description] = ""
    return field_json


def _build_szjk_indexs_payload(
    *,
    indicator: dict[str, str],
    region: dict[str, str],
    start_year: int,
    end_year: int,
    dimension_id: str,
    field_json: dict[str, str] | None = None,
    page_size: int = 100,
    page_no: int = 1,
) -> dict[str, object]:
    return {
        "timeType": "1",
        "dates": [str(start_year), str(end_year)],
        "timeFrequencies": ["\u5e74\u5ea6"],
        "timeFrequency": "year",
        "dimensionId": dimension_id,
        "indexIds": [indicator["code"]],
        "type": region.get("type") or "city",
        "areaCodes": [region["regionCode"]],
        "json": json.dumps(field_json or {}, ensure_ascii=False),
        "pageSize": page_size,
        "pageNo": page_no,
    }


def _records_from_cnki_result(data: object) -> list[dict[str, object]]:
    result = data.get("result", {}) if isinstance(data, dict) else {}
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if not isinstance(result, dict):
        return []
    records = result.get("records") or result.get("list") or result.get("data") or []
    return [item for item in records if isinstance(item, dict)]


def _records_to_original_table_rows(records: list[dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        source_parts = [
            str(record.get("data_source") or "").strip(),
            str(record.get("njnf") or "").strip(),
        ]
        rows.append({
            "\u5e8f\u53f7": str(index),
            "\u65f6\u95f4": _year_text(record.get("date") or record.get("time") or record.get("year") or ""),
            "\u5730\u533a": str(record.get("area") or record.get("region") or record.get("regionName") or ""),
            "\u6307\u6807\u540d\u79f0": str(record.get("indexName") or record.get("name") or record.get("index") or ""),
            "\u6570\u503c": str(record.get("value") or record.get("dataValue") or ""),
            "\u5355\u4f4d": str(record.get("unit") or record.get("unitName") or ""),
            "\u6570\u636e\u6765\u6e90": " ".join(part for part in source_parts if part),
        })
    return rows


def _filter_cnki_indexs_records(
    records: list[dict[str, object]],
    city_name: str,
    city_no: str,
    query_index: str,
    start_year: int,
    end_year: int,
) -> list[dict[str, object]]:
    return [
        record
        for record in records
        if _cnki_indexs_record_matches_request(record, city_name, city_no, query_index, start_year, end_year)
    ]


def _cnki_indexs_record_matches_request(
    record: dict[str, object],
    city_name: str,
    city_no: str,
    query_index: str,
    start_year: int,
    end_year: int,
) -> bool:
    year = _year_text(record.get("date") or record.get("time") or record.get("year") or "")
    if not year.isdigit() or not (start_year <= int(year) <= end_year):
        return False
    record_index = str(record.get("indexName") or record.get("name") or record.get("index") or "").strip()
    if _compact_cnki_name(record_index) != _compact_cnki_name(query_index):
        return False
    record_code = str(record.get("area_code") or record.get("areaCode") or record.get("regionCode") or "").strip()
    if city_no and record_code and record_code == city_no:
        return True
    record_area = str(record.get("area") or record.get("region") or record.get("regionName") or "").strip()
    return _area_text_matches_city(record_area, city_name)


def _compact_cnki_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _area_text_matches_city(area_text: str, city_name: str) -> bool:
    area = _compact_cnki_name(area_text)
    city = _compact_cnki_name(city_name)
    if not city:
        return False
    aliases = {city}
    if not re.search(r"[\u5e02\u53bf\u533a\u5dde\u76df]$", city):
        aliases.add(f"{city}\u5e02")
    if city.endswith("\u5e02"):
        aliases.add(city[:-1])
    return any(alias and alias in area for alias in aliases)


def _summarize_cnki_records(records: list[dict[str, object]]) -> str:
    sample = records[:3]
    parts = []
    for record in sample:
        parts.append(
            "/".join(
                [
                    str(record.get("date") or record.get("year") or ""),
                    str(record.get("area") or record.get("region") or ""),
                    str(record.get("indexName") or record.get("name") or ""),
                    str(record.get("value") or ""),
                ]
            )
        )
    return "; ".join(parts)


def _year_text(value: object) -> str:
    text = str(value or "").strip()
    match = re.search(r"(?:19|20)\d{2}", text)
    return match.group(0) if match else text


def _records_to_szjk_indexs_rows(
    records: list[dict[str, object]],
    city_name: str,
    query_index: str,
    start_year: int,
    end_year: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        rows.append(
            {
                "No": "",
                "Year": str(record.get("date") or record.get("time") or record.get("year") or ""),
                "Region": str(record.get("area") or record.get("region") or record.get("regionName") or ""),
                "Index": str(record.get("indexName") or record.get("name") or record.get("index") or query_index),
                "Value": str(record.get("value") or record.get("dataValue") or ""),
                "Unit": str(record.get("unit") or record.get("unitName") or ""),
            }
        )
    return _filter_rows_by_request(rows, city_name, query_index, start_year, end_year)


def _cnki_api_request_json(
    driver: object,
    page_url: str,
    endpoint: str,
    payload: dict[str, object],
    *,
    method: str = "post",
    json_payload: bool = True,
) -> dict[str, object]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("The requests package is required for CNKI API crawling.") from exc

    session = requests.Session()
    try:
        for cookie in driver.get_cookies():
            session.cookies.set(cookie.get("name"), cookie.get("value"), domain=cookie.get("domain"), path=cookie.get("path", "/"))
    except Exception:
        pass
    tokens: dict[str, str] = {}
    try:
        tokens = driver.execute_script(
            """
            return {
              token: localStorage.getItem('Admin-Token') || '',
              castoken: localStorage.getItem('castoken') || '',
              lid: localStorage.getItem('LID') || ''
            };
            """
        ) or {}
    except Exception:
        tokens = {}

    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://szjk.cnki.net"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": origin,
        "Referer": page_url,
        "dimensionId": _dimension_id_from_url(page_url),
        "subSystemIndex": "2",
        "User-Agent": "Mozilla/5.0",
    }
    if tokens.get("token"):
        headers["X-Access-Token"] = tokens["token"]
    if tokens.get("castoken"):
        headers["CAS-Access-Token"] = tokens["castoken"]
    if tokens.get("lid"):
        headers["Lid"] = tokens["lid"]

    url = _api_base_from_url(page_url) + endpoint
    if method.lower() == "get":
        response = session.get(url, params=payload, headers=headers, timeout=30)
    elif json_payload:
        headers["Content-Type"] = "application/json;charset=UTF-8"
        response = session.post(url, json=payload, headers=headers, timeout=50)
    else:
        response = session.post(url, data=payload, headers=headers, timeout=50)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"CNKI API did not return JSON from {endpoint}: {response.text[:200]}") from exc
    return data if isinstance(data, dict) else {"result": data}


def _html_has_year_headers(html: str, start_year: int, end_year: int) -> bool:
    parser = _TableParser()
    parser.feed(html)
    required = {f"{start_year}年", f"{end_year}年"}
    for cells in parser.rows:
        compact = {cell.replace(" ", "") for cell in cells}
        if required.issubset(compact):
            return True
    return False


def _wait_for_szjk_year_headers(driver: object, start_year: int, end_year: int, timeout: float) -> bool:
    deadline = time.time() + max(timeout, 0)
    while True:
        try:
            html = str(getattr(driver, "page_source", "") or "")
            if _html_has_year_headers(html, start_year, end_year):
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _wait_for_szjk_indexs_results(driver: object, city_name: str, keyword: str, start_year: int, end_year: int, timeout: float) -> bool:
    deadline = time.time() + max(timeout, 0)
    while True:
        try:
            rows = _filter_rows_by_request(parse_szjk_rows(str(getattr(driver, "page_source", "") or ""), city_name, keyword), city_name, keyword, start_year, end_year)
            if rows:
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _is_szjk_search_url(url: str) -> bool:
    lowered = url.lower()
    return "szjk.cnki.net" in lowered and (
        "data-indexs" in lowered or "data-easysearchreplace" in lowered or "data-easysearch" in lowered
    )


def _is_szjk_indexs_url(url: str) -> bool:
    return "szjk.cnki.net" in url.lower() and "data-indexs" in url.lower()


def _is_browser_session_lost(error: str) -> bool:
    lowered = error.lower()
    return (
        "invalidsessionidexception" in lowered
        or "without establishing a connection" in lowered
        or "failed to decode response from marionette" in lowered
        or "tried to run command" in lowered and "connection" in lowered
    )


def _wait_for_manual_login(seconds: int, log: Callable[[str], None]) -> None:
    if seconds <= 0:
        return
    log(f"Please complete CNKI login or verification in the opened browser. Crawling continues in {seconds} seconds.")
    for remaining in range(seconds, 0, -1):
        if remaining == seconds or remaining % 10 == 0 or remaining <= 5:
            log(f"Waiting for login: {remaining} seconds remaining")
        time.sleep(1)


def _retry_bool(action: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
    deadline = time.time() + max(timeout, 0)
    while True:
        if action():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)


def _create_selenium_driver(config: CrawlerConfig):
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options as FirefoxOptions
    except ImportError as exc:
        raise RuntimeError("CNKI crawling requires selenium. Install Step3Core requirements and a browser driver first.") from exc

    if config.browser.lower() != "firefox":
        raise ValueError("Only firefox is currently supported")
    options = FirefoxOptions()
    options.headless = config.headless
    if config.browser_profile_dir:
        options.profile = str(config.browser_profile_dir)
    return webdriver.Firefox(options=options)


def _open_szjk_data_analysis(driver: object) -> bool:
    script = r"""
    // SZJK_DATA_ANALYSIS_TAB
    const labels = ['数据分析', '指标选择'];
    const visible = el => !!(el && el.offsetParent !== null);
    const clickable = Array.from(document.querySelectorAll('a, button, [role="tab"], [role="button"], li, div, span'))
      .find(el => visible(el) && labels.some(label => (el.innerText || el.textContent || '').trim() === label));
    if (clickable) {
      clickable.click();
      return true;
    }
    return false;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _open_szjk_left_tab(driver: object, label: str) -> bool:
    script = r"""
    // SZJK_LEFT_DIMENSION_TAB
    const label = arguments[0];
    const visible = el => !!(el && el.offsetParent !== null);
    const candidates = Array.from(document.querySelectorAll('a, button, [role="tab"], [role="button"], li, div, span'))
      .filter(el => {
        if (!visible(el)) return false;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, '').trim();
        return text === label || text.includes(label);
      });
    const target = candidates
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .filter(item => item.rect.left < Math.min(420, window.innerWidth * 0.35) && item.rect.top > 120)
      .sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)[0]?.el
      || candidates[0];
    if (!target) return false;
    target.click();
    return true;
    """
    try:
        return bool(driver.execute_script(script, label))
    except Exception:
        return False


def _search_szjk_indicator_tree(driver: object, keyword: str) -> bool:
    script = r"""
    // SZJK_INDICATOR_TREE_SEARCH
    const keyword = arguments[0];
    const query = /[\u4e00-\u9fff]/.test(keyword) ? keyword.replace(/\s+/g, '') : keyword;
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, nextValue) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(el, nextValue);
      else el.value = nextValue;
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
    }
    function textOf(el) {
      return [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value]
        .filter(Boolean).join(' ');
    }
    function isGlobalHeader(input) {
      const rect = input.getBoundingClientRect();
      return rect.top < 150;
    }
    function inputScore(input) {
      const rect = input.getBoundingClientRect();
      const text = textOf(input);
      const panel = input.closest('div, section, aside, form, .el-card, .el-tabs__content, .el-tree') || input.parentElement;
      const panelText = panel ? (panel.innerText || panel.textContent || '') : '';
      let score = 0;
      if (/搜索体系\/指标|搜索体系|体系\/指标|搜索指标|检索指标/.test(text)) score += 100;
      if (/指标/.test(text)) score += 20;
      if (/指标选择|选择指标/.test(panelText)) score += 30;
      if (/地区选择|请选择省\/市地区|请搜索省\/市地区/.test(panelText)) score -= 80;
      if (rect.top >= 150 && rect.top <= 420) score += 10;
      if (rect.left >= 320 && rect.left <= Math.min(900, window.innerWidth * 0.75)) score += 10;
      if (isGlobalHeader(input)) score -= 100;
      return score;
    }
    const inputs = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el))
      .map(el => ({el, text: textOf(el), rect: el.getBoundingClientRect(), score: inputScore(el)}))
      .filter(item => item.score > 0 && /体系|指标|搜索|检索|keyword|search/i.test(item.text))
      .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    const target = inputs[0]?.el;
    if (!target) return false;
    target.focus();
    setNativeValue(target, query);
    const container = target.closest('aside, section, form, .el-card, .left, .side, .tree, div') || target.parentElement || document;
    const action = Array.from(container.querySelectorAll('button, [role="button"], .el-icon-search, i, span'))
      .find(el => visible(el) && /搜索|检索|查询|search/i.test(el.innerText || el.title || el.className || ''));
    if (action) action.click();
    return true;
    """
    try:
        return bool(driver.execute_script(script, keyword))
    except Exception:
        return False


def _search_szjk_region_tree(driver: object, city_name: str) -> bool:
    script = r"""
    // SZJK_REGION_TREE_SEARCH
    const city = arguments[0];
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, nextValue) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(el, nextValue);
      else el.value = nextValue;
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
    }
    function textOf(el) {
      return [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value]
        .filter(Boolean).join(' ');
    }
    function inputScore(input) {
      const rect = input.getBoundingClientRect();
      const text = textOf(input);
      const panel = input.closest('div, section, aside, form, .el-card, .el-tabs__content, .el-tree') || input.parentElement;
      const panelText = panel ? (panel.innerText || panel.textContent || '') : '';
      let score = 0;
      if (/请搜索省\/市地区|搜索省\/市地区|省\/市地区|地区/.test(text)) score += 100;
      if (/地区选择|选择地区|城市群|中国|省/.test(panelText)) score += 30;
      if (/指标选择|选择指标|体系\/指标/.test(panelText)) score -= 80;
      if (rect.top >= 150 && rect.top <= 420) score += 10;
      if (rect.left < Math.min(360, window.innerWidth * 0.35)) score += 10;
      if (rect.top < 150) score -= 100;
      return score;
    }
    const inputs = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el))
      .map(el => ({el, text: textOf(el), rect: el.getBoundingClientRect(), score: inputScore(el)}))
      .filter(item => item.score > 0 && /省|市|地区|城市|region|search/i.test(item.text))
      .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    const target = inputs[0]?.el;
    if (!target) return false;
    target.focus();
    setNativeValue(target, city);
    return true;
    """
    try:
        return bool(driver.execute_script(script, city_name))
    except Exception:
        return False


def _select_szjk_region(driver: object, city_name: str) -> bool:
    script = r"""
    // SZJK_REGION_SELECTOR
    const city = arguments[0].replace(/\s+/g, '').trim();
    const visible = el => !!(el && el.offsetParent !== null);
    const normalize = text => (text || '').replace(/\s+/g, '').trim();
    const names = new Set([city]);
    if (city && !/[市省区县]$/.test(city)) {
      names.add(`${city}市`);
      names.add(`${city}省`);
      names.add(`${city}区`);
      names.add(`${city}县`);
    }
    function checkboxClick(target) {
      const checkbox = target.querySelector('input[type="checkbox"]');
      if (checkbox) {
        const checked = checkbox.checked || checkbox.getAttribute('aria-checked') === 'true';
        if (!checked) {
          checkbox.click();
          checkbox.dispatchEvent(new Event('change', {bubbles: true}));
        }
        return true;
      }
      const box = target.querySelector('.el-checkbox__inner, .el-checkbox__input, .el-checkbox, [role="checkbox"]');
      if (box) {
        const checked = box.classList?.contains('is-checked') || box.getAttribute?.('aria-checked') === 'true';
        if (!checked) box.click();
        return true;
      }
      target.click();
      return true;
    }
    const containers = Array.from(document.querySelectorAll('label, li, .el-tree-node, .el-tree-node__content, .el-checkbox, [role="treeitem"], [title], [aria-label]'))
      .filter(el => visible(el))
      .map(el => ({
        el,
        rect: el.getBoundingClientRect(),
        text: normalize([el.getAttribute?.('title'), el.getAttribute?.('aria-label'), el.innerText, el.textContent].filter(Boolean).join(' '))
      }))
      .filter(item => Array.from(names).some(name => item.text === name || item.text.endsWith(name)))
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    for (const item of containers) {
      const target = item.el.closest('label, li, .el-tree-node, .el-checkbox, [role="treeitem"]') || item.el;
      return checkboxClick(target);
    }
    return false;
    """
    try:
        return bool(driver.execute_script(script, city_name))
    except Exception:
        return False


def _fill_szjk_field(driver: object, label_candidates: list[str], value: str) -> bool:
    script = """
    const labels = arguments[0];
    const value = arguments[1];
    const controls = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && el.offsetParent !== null);
    function textNear(el) {
      const attrs = [el.placeholder, el.ariaLabel, el.name, el.id, el.title].filter(Boolean).join(' ');
      const parent = el.closest('div, label, form, section, li');
      return attrs + ' ' + (parent ? parent.innerText : '');
    }
    const target = controls.find(el => labels.some(label => textNear(el).includes(label)));
    if (!target) return false;
    target.focus();
    target.value = '';
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.value = value;
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
    """
    try:
        return bool(driver.execute_script(script, label_candidates, value))
    except Exception:
        return False


def _clear_szjk_selected_content(driver: object) -> bool:
    script = r"""
    // SZJK_CLEAR_SELECTED_CONTENT
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click();
    }
    let changed = false;
    const panels = Array.from(document.querySelectorAll('div, section, form'))
      .filter(el => visible(el) && /已选内容|已选地区|已选指标/.test(el.innerText || el.textContent || ''));
    const roots = panels.length ? panels : [document];
    const clearButtons = roots.flatMap(root => Array.from(root.querySelectorAll('a, button, span, [role="button"]')))
      .filter(el => visible(el) && /全部清除/.test(el.innerText || el.textContent || el.value || ''));
    for (const button of clearButtons) {
      robustClick(button);
      changed = true;
    }
    const removeTags = roots.flatMap(root => Array.from(root.querySelectorAll('i, span, a, button, [role="button"]')))
      .filter(el => visible(el) && /×|x|close|删除|移除/i.test(el.innerText || el.textContent || el.className || el.title || ''));
    for (const tag of removeTags) {
      robustClick(tag);
      changed = true;
    }
    return changed;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _select_szjk_indicator(driver: object, exact_index: str) -> bool:
    script = r"""
    // INDICATOR_EXACT_SELECTOR
    const exact = arguments[0].replace(/\s+/g, ' ').trim();
    const compactExact = arguments[0].replace(/\s+/g, '').trim();
    const visible = el => !!(el && el.offsetParent !== null);
    const normalize = text => (text || '').replace(/\s+/g, ' ').trim();
    const compact = text => (text || '').replace(/\s+/g, '').trim();
    function fullText(el) {
      const values = [
        el.getAttribute?.('title'),
        el.getAttribute?.('aria-label'),
        el.dataset?.title,
        el.dataset?.name,
        el.innerText,
        el.textContent
      ].map(normalize).filter(Boolean);
      return values.find(value => value === exact) || values.join(' ');
    }
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click();
    }
    function checkboxClick(target) {
      const box = target.querySelector('.el-checkbox__inner, .el-checkbox__input, .el-checkbox, [role="checkbox"]');
      if (box) {
        const owner = box.closest('.el-checkbox, .el-checkbox__input, [role="checkbox"]') || box;
        const checked = owner.classList?.contains('is-checked') || owner.getAttribute?.('aria-checked') === 'true';
        if (!checked) robustClick(box);
        return true;
      }
      const checkbox = target.querySelector('input[type="checkbox"]');
      if (checkbox) {
        const owner = checkbox.closest('label, .el-checkbox, .el-tree-node__content, .el-tree-node') || checkbox;
        const checked = checkbox.checked || owner.classList?.contains('is-checked') || checkbox.getAttribute('aria-checked') === 'true';
        if (!checked) {
          robustClick(owner);
          checkbox.dispatchEvent(new Event('change', {bubbles: true}));
        }
        return true;
      }
      robustClick(target);
      return true;
    }
    const containers = Array.from(document.querySelectorAll('label, li, .el-tree-node, .el-tree-node__content, .el-checkbox, [role="treeitem"], [title], [aria-label]'))
      .filter(el => visible(el) && (
        normalize(fullText(el)) === exact
        || compact(fullText(el)) === compactExact
        || fullText(el).split(/\s{2,}/).map(normalize).includes(exact)
        || fullText(el).split(/\s{2,}/).map(compact).includes(compactExact)
      ));
    for (const container of containers) {
      const target = container.closest('label, li, .el-tree-node, .el-checkbox, [role="treeitem"]') || container;
      return checkboxClick(target);
    }
    const allCheckboxes = Array.from(document.querySelectorAll('input[type="checkbox"], .el-checkbox, [role="checkbox"]')).filter(visible);
    for (const box of allCheckboxes) {
      const container = box.closest('label, li, .el-tree-node, .el-checkbox, [role="treeitem"], div');
      if (normalize(fullText(container || box)) === exact || compact(fullText(container || box)) === compactExact) {
        const checked = box.checked || box.classList?.contains('is-checked') || box.getAttribute?.('aria-checked') === 'true';
        if (!checked) robustClick(box);
        return true;
      }
    }
    return false;
    """
    try:
        return bool(driver.execute_script(script, exact_index))
    except Exception:
        return False


def _fill_szjk_indexs_indicator_name(driver: object, index_keyword: str) -> bool:
    script = r"""
    // SZJK_DATA_INDEXS_INDICATOR_NAME
    const indexKeyword = arguments[0];
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, value) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      el.removeAttribute?.('readonly');
      el.dispatchEvent(new Event('focus', eventOptions));
      if (descriptor && descriptor.set) descriptor.set.call(el, value);
      else el.value = value;
      try {
        el.dispatchEvent(new InputEvent('beforeinput', {bubbles: true, cancelable: true, inputType: 'insertText', data: value}));
      } catch (e) {}
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
    }
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    function nearText(el) {
      const parent = el.closest('div, label, form, section, li, .el-form-item') || el.parentElement;
      const previous = parent?.previousElementSibling;
      return [
        el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value,
        parent?.innerText, previous?.innerText
      ].filter(Boolean).join(' ');
    }
    function isTextInput(el) {
      const tag = (el.tagName || '').toLowerCase();
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return tag === 'textarea' || ['text', 'search', ''].includes(type);
    }
    const labels = ['\u6307\u6807\u540d\u79f0', '\u8bf7\u8f93\u5165\u6307\u6807', '\u68c0\u7d22\u6307\u6807'];
    const controls = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el) && isTextInput(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        const text = nearText(el);
        let score = labels.some(label => text.includes(label)) ? 100 : 0;
        if (rect.top >= 70 && rect.top <= 240) score += 20;
        if (rect.left < window.innerWidth * 0.45) score += 10;
        if (rect.top < 60) score -= 80;
        return {el, rect, score};
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    const input = controls[0]?.el;
    if (!input) return false;
    robustClick(input);
    input.focus();
    setNativeValue(input, '');
    setNativeValue(input, indexKeyword);
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
    input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', bubbles: true}));
    input.dispatchEvent(new Event('focusout', eventOptions));
    input.blur();
    return true;
    """
    try:
        return bool(driver.execute_script(script, index_keyword))
    except Exception:
        return False


def _fill_szjk_indexs_region(driver: object, city_name: str, wait_seconds: float = 1.0) -> bool:
    open_script = r"""
    // SZJK_DATA_INDEXS_REGION_OPEN
    const cityName = arguments[0];
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, value) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      el.removeAttribute?.('readonly');
      el.dispatchEvent(new Event('focus', eventOptions));
      if (descriptor && descriptor.set) descriptor.set.call(el, value);
      else el.value = value;
      try {
        el.dispatchEvent(new InputEvent('beforeinput', {bubbles: true, cancelable: true, inputType: 'insertText', data: value}));
      } catch (e) {}
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
    }
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    function nearText(el) {
      const parent = el.closest('div, label, form, section, li, .el-form-item') || el.parentElement;
      const previous = parent?.previousElementSibling;
      return [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value, parent?.innerText, previous?.innerText]
        .filter(Boolean).join(' ');
    }
    function isTextInput(el) {
      const tag = (el.tagName || '').toLowerCase();
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return tag === 'textarea' || ['text', 'search', ''].includes(type);
    }
    const labels = ['\u6307\u6807\u5730\u533a', '\u5730\u533a'];
    const controls = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el) && isTextInput(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        const text = nearText(el);
        let score = labels.some(label => text.includes(label)) ? 100 : 0;
        if (rect.top >= 70 && rect.top <= 240) score += 20;
        if (rect.left >= window.innerWidth * 0.2 && rect.left <= window.innerWidth * 0.75) score += 10;
        return {el, rect, score};
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    const input = controls[0]?.el;
    if (!input) return false;
    robustClick(input);
    input.focus();
    setNativeValue(input, '');
    setNativeValue(input, cityName);
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', code: 'ArrowDown', bubbles: true}));
    input.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowDown', code: 'ArrowDown', bubbles: true}));
    return true;
    """
    select_script = r"""
    // SZJK_DATA_INDEXS_REGION_SELECT
    const cityName = arguments[0].replace(/\s+/g, '');
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    const names = new Set([cityName]);
    if (cityName && !/[\u5e02\u53bf\u533a\u5dde\u76df]$/.test(cityName)) names.add(`${cityName}\u5e02`);
    if (cityName.endsWith('\u5e02')) names.add(cityName.slice(0, -1));
    const tableTop = Math.min(
      ...Array.from(document.querySelectorAll('table, .el-table, [class*="table"]'))
        .filter(visible)
        .map(el => el.getBoundingClientRect().top)
        .filter(top => top > 0),
      window.innerHeight
    );
    const optionSelectors = [
      '.el-select-dropdown__item', '.el-select-dropdown__item span',
      '.el-cascader-node', '.el-cascader-node__label', '.el-cascader-menu__list li',
      '.el-autocomplete-suggestion li', '.el-autocomplete-suggestion__wrap li',
      '.el-popper li', '.el-popper span', '.el-popper div',
      '[role="option"]', '.el-scrollbar__view li', 'li', 'span', 'div'
    ];
    const options = Array.from(document.querySelectorAll(optionSelectors.join(',')))
      .filter(visible)
      .map(el => ({
        el,
        text: (el.innerText || el.textContent || '').replace(/\s+/g, '').trim(),
        rect: el.getBoundingClientRect(),
        inPopup: !!el.closest('.el-popper, .el-select-dropdown, .el-cascader-panel, .el-autocomplete-suggestion'),
        isFloating: (() => {
          const rect = el.getBoundingClientRect();
          return rect.top >= 120 && rect.top < tableTop && rect.left > 250 && rect.width < window.innerWidth * 0.55;
        })()
      }))
      .filter(item => (item.inPopup || item.isFloating) && Array.from(names).some(name => item.text === name || item.text.endsWith(name)))
      .sort((a, b) => {
        const exactA = Array.from(names).some(name => a.text === name) ? 0 : 1;
        const exactB = Array.from(names).some(name => b.text === name) ? 0 : 1;
        const popupA = a.inPopup ? 0 : 1;
        const popupB = b.inPopup ? 0 : 1;
        return exactA - exactB || popupA - popupB || (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height) || a.rect.top - b.rect.top;
      });
    if (options[0]) {
      const target = options[0].el.closest('.el-select-dropdown__item, .el-cascader-node, li, [role="option"]') || options[0].el;
      robustClick(target);
      document.body.click();
      return true;
    }
    const active = document.activeElement;
    if (active) {
      active.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
      active.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', bubbles: true}));
    }
    return false;
    """
    try:
        if not bool(driver.execute_script(open_script, city_name)):
            return False
        deadline = time.time() + max(wait_seconds, 0)
        while True:
            if bool(driver.execute_script(select_script, city_name)):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.25)
    except Exception:
        return False


def _select_szjk_indexs_annual_frequency(driver: object) -> bool:
    script = r"""
    // SZJK_DATA_INDEXS_ANNUAL
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    const candidates = Array.from(document.querySelectorAll('label, .el-radio, span, div, input, [role="radio"]'))
      .filter(visible)
      .map(el => ({el, text: (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ''), rect: el.getBoundingClientRect()}))
      .filter(item => item.text.includes('\u5e74\u5ea6') && item.rect.top >= 70 && item.rect.top <= 240)
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    const target = candidates[0]?.el?.closest('label, .el-radio, [role="radio"]') || candidates[0]?.el;
    if (!target) return false;
    robustClick(target);
    return true;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _fill_szjk_indexs_time(driver: object, start_year: int, end_year: int, wait_seconds: float = 1.0) -> bool:
    open_script = r"""
    // SZJK_DATA_INDEXS_TIME_OPEN
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    function nearText(el) {
      const parent = el.closest('div, label, form, section, li, .el-form-item') || el.parentElement;
      const previous = parent?.previousElementSibling;
      return [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value, parent?.innerText, previous?.innerText]
        .filter(Boolean).join(' ');
    }
    function isTextInput(el) {
      const tag = (el.tagName || '').toLowerCase();
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return tag === 'textarea' || ['text', 'search', ''].includes(type);
    }
    const controls = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el) && isTextInput(el))
      .map(el => {
        const rect = el.getBoundingClientRect();
        const text = nearText(el);
        let score = text.includes('\u6307\u6807\u65f6\u95f4') ? 100 : 0;
        if (/\d{4}\s*\u5e74\s*-\s*\d{4}\s*\u5e74/.test(el.value || '')) score += 60;
        if (rect.top >= 70 && rect.top <= 240) score += 20;
        if (rect.left >= window.innerWidth * 0.55) score += 10;
        return {el, rect, score};
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    const input = controls[0]?.el;
    if (!input) return false;
    robustClick(input);
    input.focus();
    return true;
    """
    set_script = r"""
    // SZJK_DATA_INDEXS_TIME_SET
    const startYear = String(arguments[0]);
    const endYear = String(arguments[1]);
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, value) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      el.removeAttribute?.('readonly');
      el.dispatchEvent(new Event('focus', eventOptions));
      if (descriptor && descriptor.set) descriptor.set.call(el, value);
      else el.value = value;
      try {
        el.dispatchEvent(new InputEvent('beforeinput', {bubbles: true, cancelable: true, inputType: 'insertText', data: value}));
      } catch (e) {}
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
      el.dispatchEvent(new Event('focusout', eventOptions));
    }
    function click(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      el.click?.();
    }
    const popupContainers = Array.from(document.querySelectorAll('.el-picker-panel, .el-popper, .el-popover, [role="dialog"], div'))
      .filter(el => {
        if (!visible(el)) return false;
        const text = el.innerText || el.textContent || '';
        const rect = el.getBoundingClientRect();
        return /(\u5df2\u9009\u65f6\u95f4|\u6700\u8fd1\u4e00\u5e74|\u6700\u8fd1\u4e94\u5e74|\u6700\u8fd1\u5341\u5e74|\d{4}\u5e74)/.test(text)
          && rect.top >= 40
          && rect.height > 40
          && rect.width > 120;
      })
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    const popupRoot = popupContainers.find(item => (item.el.innerText || item.el.textContent || '').includes('\u5df2\u9009\u65f6\u95f4'))?.el
      || popupContainers[0]?.el
      || document;
    const popupInputs = Array.from(popupRoot.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el) && ((el.tagName || '').toLowerCase() === 'textarea' || ['text', 'search', ''].includes((el.getAttribute('type') || 'text').toLowerCase())))
      .map(el => ({el, rect: el.getBoundingClientRect(), value: el.value || ''}))
      .filter(item => item.rect.top >= 40 && (/\d{4}/.test(item.value) || item.rect.width >= 60))
      .sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)
      .map(item => item.el);
    if (popupInputs.length >= 2) {
      popupInputs[0].focus();
      setNativeValue(popupInputs[0], `${startYear}\u5e74`);
      popupInputs[1].focus();
      setNativeValue(popupInputs[1], `${endYear}\u5e74`);
      popupInputs[1].dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
      popupInputs[1].dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', bubbles: true}));
      popupInputs[1].blur();
      const panel = popupInputs[1].closest('.el-picker-panel, .el-popper, [role="dialog"]') || document;
      const confirm = Array.from(panel.querySelectorAll('button, [role="button"]'))
        .find(el => visible(el) && /(\u786e\u5b9a|OK|Confirm)/i.test(el.innerText || el.textContent || el.value || ''));
      if (confirm) click(confirm);
      document.body.click();
      return true;
    }
    const rangeValue = `${startYear}\u5e74-${endYear}\u5e74`;
    const inputs = Array.from(document.querySelectorAll('input, textarea'))
      .filter(el => !el.disabled && visible(el) && ((el.tagName || '').toLowerCase() === 'textarea' || ['text', 'search', ''].includes((el.getAttribute('type') || 'text').toLowerCase())))
      .map(el => ({el, text: [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value, el.closest('div, label, form, section, li, .el-form-item')?.innerText].filter(Boolean).join(' '), rect: el.getBoundingClientRect()}))
      .filter(item => item.text.includes('\u6307\u6807\u65f6\u95f4') || /\d{4}\s*\u5e74\s*-\s*\d{4}\s*\u5e74/.test(item.el.value || ''))
      .sort((a, b) => a.rect.top - b.rect.top || b.rect.left - a.rect.left);
    const input = inputs[0]?.el;
    if (!input) return false;
    setNativeValue(input, rangeValue);
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
    input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', bubbles: true}));
    return true;
    """
    try:
        if not bool(driver.execute_script(open_script)):
            return False
        deadline = time.time() + max(wait_seconds, 0)
        while True:
            if bool(driver.execute_script(set_script, start_year, end_year)):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.25)
    except Exception:
        return False


def _fill_szjk_indexs_form(driver: object, index_keyword: str, city_name: str, start_year: int, end_year: int) -> bool:
    return (
        _fill_szjk_indexs_indicator_name(driver, index_keyword)
        and _fill_szjk_indexs_region(driver, city_name)
        and _select_szjk_indexs_annual_frequency(driver)
        and _fill_szjk_indexs_time(driver, start_year, end_year)
    )


def _fill_and_verify_szjk_indexs_form(
    driver: object,
    index_keyword: str,
    city_name: str,
    start_year: int,
    end_year: int,
    *,
    timeout: float,
) -> dict[str, object]:
    state: dict[str, object] = {"ready": False, "summary": "state was not checked"}
    deadline = time.time() + max(timeout, 0)
    tried_full_form = False
    while True:
        if not tried_full_form:
            _fill_szjk_indexs_form(driver, index_keyword, city_name, start_year, end_year)
            tried_full_form = True
        state = _read_szjk_indexs_form_state(driver, index_keyword, city_name, start_year, end_year)
        if state.get("ready"):
            return state
        if time.time() >= deadline:
            return state
        if not state.get("has_indicator"):
            _fill_szjk_indexs_indicator_name(driver, index_keyword)
        if not state.get("has_region"):
            _fill_szjk_indexs_region(driver, city_name)
        if not state.get("has_time"):
            _select_szjk_indexs_annual_frequency(driver)
            _fill_szjk_indexs_time(driver, start_year, end_year)
        time.sleep(0.25)


def _read_szjk_indexs_form_state(driver: object, index_keyword: str, city_name: str, start_year: int, end_year: int) -> dict[str, object]:
    script = r"""
    // SZJK_DATA_INDEXS_FORM_STATE
    const expectedIndex = String(arguments[0] || '').replace(/\s+/g, '');
    const cityName = String(arguments[1] || '').replace(/\s+/g, '');
    const startYear = String(arguments[2]);
    const endYear = String(arguments[3]);
    const visible = el => !!(el && el.offsetParent !== null);
    const compact = text => String(text || '').replace(/\s+/g, '');
    function nearText(el) {
      const parent = el.closest('div, label, form, section, li, .el-form-item') || el.parentElement;
      const previous = parent?.previousElementSibling;
      return [
        el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.value,
        parent?.innerText, previous?.innerText
      ].filter(Boolean).join(' ');
    }
    function textOf(el) {
      if (!el) return '';
      const shell = el.closest?.('.el-select, .el-date-editor, .el-input, .el-form-item, [class*="select"], [class*="date"]');
      return [
        el.value,
        el.getAttribute?.('title'),
        el.getAttribute?.('aria-label'),
        shell?.getAttribute?.('title'),
        shell?.innerText,
        shell?.textContent,
        el.innerText,
        el.textContent
      ].filter(Boolean).join(' ');
    }
    function scoreControl(el, labels, leftMin, leftMax) {
      const rect = el.getBoundingClientRect();
      const text = nearText(el);
      let score = labels.some(label => text.includes(label)) ? 100 : 0;
      if (rect.top >= 70 && rect.top <= 280) score += 20;
      if (rect.left >= leftMin && rect.left <= leftMax) score += 10;
      if (rect.top < 60) score -= 80;
      return {el, rect, score, text};
    }
    function isTextInput(el) {
      const tag = (el.tagName || '').toLowerCase();
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return tag === 'textarea' || ['text', 'search', ''].includes(type);
    }
    function bestInput(labels, leftMin, leftMax) {
      return Array.from(document.querySelectorAll('input, textarea'))
        .filter(el => !el.disabled && visible(el) && isTextInput(el))
        .map(el => scoreControl(el, labels, leftMin, leftMax))
        .filter(item => item.score > 0)
        .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left)[0]?.el || null;
    }
    const indicatorInput = bestInput(['指标名称', '请输入指标', '检索指标'], 0, window.innerWidth * 0.5);
    const regionInput = bestInput(['指标地区', '地区'], window.innerWidth * 0.15, window.innerWidth * 0.75);
    const timeInput = bestInput(['指标时间'], window.innerWidth * 0.55, window.innerWidth);
    const indicatorValue = compact(textOf(indicatorInput));
    const regionValue = compact(textOf(regionInput));
    const timeValue = compact(textOf(timeInput));
    const cityNames = new Set([cityName]);
    if (cityName && !/[\u5e02\u53bf\u533a\u5dde\u76df]$/.test(cityName)) cityNames.add(`${cityName}\u5e02`);
    if (cityName.endsWith('\u5e02')) cityNames.add(cityName.slice(0, -1));
    const regionPopupOpen = Array.from(document.querySelectorAll('.el-select-dropdown, .el-cascader-panel, .el-autocomplete-suggestion, .el-popper'))
      .some(el => {
        if (!visible(el)) return false;
        const text = compact(el.innerText || el.textContent || '');
        const rect = el.getBoundingClientRect();
        return rect.width > 80 && rect.height > 40 && Array.from(cityNames).some(name => text.includes(name));
      });
    const hasIndicator = !!expectedIndex && (indicatorValue === expectedIndex || indicatorValue.includes(expectedIndex));
    const hasRegion = !regionPopupOpen && Array.from(cityNames).some(name => regionValue === name || regionValue.includes(name));
    const hasTime = timeValue.includes(startYear) && timeValue.includes(endYear);
    return {
      ready: hasIndicator && hasRegion && hasTime,
      has_indicator: hasIndicator,
      has_region: hasRegion,
      has_time: hasTime,
      indicator: indicatorInput ? (indicatorInput.value || '') : '',
      region: regionInput ? (regionInput.value || '') : '',
      time: timeInput ? (timeInput.value || '') : '',
      summary: `indicator=${indicatorInput ? indicatorInput.value : '<missing>'}; region=${regionInput ? regionInput.value : '<missing>'}; time=${timeInput ? timeInput.value : '<missing>'}; regionPopupOpen=${regionPopupOpen}; expected=${arguments[0]} / ${arguments[1]} / ${startYear}-${endYear}`
    };
    """
    try:
        state = driver.execute_script(script, index_keyword, city_name, start_year, end_year)
    except Exception:
        return {"ready": False, "has_indicator": False, "has_region": False, "has_time": False, "summary": "state read failed"}
    return state if isinstance(state, dict) else {"ready": False, "has_indicator": False, "has_region": False, "has_time": False, "summary": "invalid state"}


def _click_szjk_indexs_query(driver: object) -> bool:
    script = r"""
    // SZJK_DATA_INDEXS_QUERY
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click?.();
    }
    const tableTop = Math.min(
      ...Array.from(document.querySelectorAll('table, .el-table, [class*="table"]'))
        .filter(visible)
        .map(el => el.getBoundingClientRect().top)
        .filter(top => top > 0),
      window.innerHeight
    );
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], a'))
      .filter(el => visible(el) && (el.innerText || el.textContent || el.value || '').trim() === '\u67e5\u8be2')
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .filter(item => item.rect.top < tableTop && item.rect.top >= 60)
      .sort((a, b) => b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    const target = buttons[0]?.el;
    if (!target) return false;
    robustClick(target);
    return true;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _fill_szjk_time_range(driver: object, start_year: int, end_year: int) -> bool:
    value = f"{start_year}年-{end_year}年"
    script = r"""
    const value = arguments[0];
    const startYear = String(arguments[1]);
    const endYear = String(arguments[2]);
    const startYearLabel = `${arguments[1]}年`;
    const endYearLabel = `${arguments[2]}年`;
    const labels = ['按照时间范围选择', '按时间范围选择', '已选时间', '时间范围', '时间', '年份', '年度'];
    const visible = el => !!(el && el.offsetParent !== null);
    const eventOptions = {bubbles: true};
    function setNativeValue(el, nextValue) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(el, nextValue);
      else el.value = nextValue;
      el.dispatchEvent(new Event('input', eventOptions));
      el.dispatchEvent(new Event('change', eventOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', eventOptions));
      el.blur();
    }
    function setYearValue(el, numericValue, labelValue) {
      const role = (el.getAttribute('role') || '').toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (type === 'number' || role === 'spinbutton') {
        setNativeValue(el, numericValue);
      } else {
        setNativeValue(el, labelValue);
      }
    }
    function nearText(el) {
      return [el.placeholder, el.ariaLabel, el.name, el.id, el.title, el.innerText, el.textContent,
        el.closest('div, label, li, section, form')?.innerText]
        .filter(Boolean).join(' ');
    }
    const yearPairSelectors = [
      '.el-picker-panel input',
      '.el-popper input',
      '[role="dialog"] input',
      '.el-date-range-picker input',
      '.el-picker-panel__body input'
    ];
    function setPair(inputs) {
      const usable = inputs.filter(el => !el.disabled && visible(el));
      if (usable.length < 2) return false;
      usable[0].focus();
      setYearValue(usable[0], startYear, startYearLabel);
      usable[1].focus();
      setYearValue(usable[1], endYear, endYearLabel);
      usable[1].dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
      usable[1].dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', bubbles: true}));
      const panel = usable[1].closest('.el-picker-panel, .el-popper, [role="dialog"]');
      const confirm = panel ? Array.from(panel.querySelectorAll('button, [role="button"]'))
        .find(el => visible(el) && /确定|OK|Confirm/.test(el.innerText || el.value || '')) : null;
      if (confirm) confirm.click();
      document.body.click();
      return true;
    }
    function setAnyPopupPair() {
      for (const selector of yearPairSelectors) {
        const popupInputs = Array.from(document.querySelectorAll(selector));
        if (setPair(popupInputs)) return true;
      }
      return false;
    }
    const containers = Array.from(document.querySelectorAll('div, li, section, form, label'))
      .filter(el => visible(el) && labels.some(label => (el.innerText || '').includes(label)));
    for (const container of containers) {
      const nested = Array.from(container.querySelectorAll('input, textarea')).filter(el => !el.disabled && visible(el));
      if (nested.length === 1) {
        nested[0].focus();
        nested[0].click();
        if (setAnyPopupPair()) return true;
        return false;
      }
      if (nested.length >= 2) {
        setPair(nested);
        return true;
      }
      const clickable = Array.from(container.querySelectorAll('.el-input, .el-date-editor, button, [role="button"], span, div'))
        .find(visible);
      if (clickable) clickable.click();
      if (setAnyPopupPair()) return true;
    }
    if (setAnyPopupPair()) return true;
    const controls = Array.from(document.querySelectorAll('input, textarea')).filter(el => !el.disabled && visible(el));
    const direct = controls.find(el => labels.some(label => nearText(el).includes(label)));
    if (direct) {
      direct.focus();
      direct.click();
      if (setAnyPopupPair()) return true;
      return false;
    }
    const popupInputs = Array.from(document.querySelectorAll('.el-picker-panel input, .el-popper input, [role="dialog"] input'))
      .filter(el => !el.disabled && visible(el));
    if (popupInputs.length === 1) {
      popupInputs[0].focus();
      setNativeValue(popupInputs[0], value);
      return true;
    }
    if (popupInputs.length >= 2) {
      setPair(popupInputs);
      return true;
    }
    return false;
    """
    try:
        return bool(driver.execute_script(script, value, start_year, end_year))
    except Exception:
        return False


def _click_szjk_action(driver: object, labels: list[str]) -> bool:
    script = """
    const labels = arguments[0];
    const elements = Array.from(document.querySelectorAll('button, a, [role="button"], .btn'));
    const target = elements.find(el => el.offsetParent !== null && labels.some(label => (el.innerText || el.value || '').includes(label)));
    if (!target) return false;
    target.click();
    return true;
    """
    try:
        return bool(driver.execute_script(script, labels))
    except Exception:
        return False


def _read_szjk_selected_content_state(driver: object, city_name: str, exact_index: str) -> dict[str, object]:
    script = r"""
    // SZJK_SELECTED_CONTENT_STATE
    const city = arguments[0].replace(/\s+/g, '');
    const compactIndex = arguments[1].replace(/\s+/g, '');
    const visible = el => !!(el && el.offsetParent !== null);
    const compact = text => (text || '').replace(/\s+/g, '');
    const cityNames = new Set([city]);
    if (city && !/[市省区县]$/.test(city)) cityNames.add(`${city}市`);
    const panels = Array.from(document.querySelectorAll('div, section, form'))
      .filter(el => visible(el) && /已选内容|已选地区|已选指标/.test(el.innerText || el.textContent || ''))
      .map(el => ({el, text: el.innerText || el.textContent || '', rect: el.getBoundingClientRect()}))
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    const panel = panels[0];
    if (!panel) return {visible: false, ready: false, has_city: false, has_index: false, region_count: 0, summary: 'selected panel not visible'};
    const text = compact(panel.text);
    const regionMatch = text.match(/已选地区[:：]?(.*?)(已选指标[:：]?|$)/);
    const indexMatch = text.match(/已选指标[:：]?(.*?)(查询|$)/);
    const regionText = regionMatch ? regionMatch[1] : '';
    const indexText = indexMatch ? indexMatch[1] : '';
    const regionItems = regionText
      .split(/[×x]/i)
      .map(item => item.replace(/全部清除/g, '').trim())
      .filter(Boolean);
    const hasCity = Array.from(cityNames).some(name => regionItems.includes(name));
    const hasIndex = indexText.includes(compactIndex);
    const ready = hasCity && hasIndex && regionItems.length === 1;
    return {
      visible: true,
      ready,
      has_city: hasCity,
      has_index: hasIndex,
      region_count: regionItems.length,
      summary: `regions=${regionItems.join('|')}; hasIndex=${hasIndex}`
    };
    """
    try:
        state = driver.execute_script(script, city_name, exact_index)
    except Exception:
        return {"visible": False, "ready": False, "has_city": False, "has_index": False, "region_count": 0, "summary": "state read failed"}
    return state if isinstance(state, dict) else {"visible": False, "ready": False, "has_city": False, "has_index": False, "region_count": 0, "summary": "invalid state"}


def _click_szjk_selected_content_query(driver: object) -> bool:
    script = r"""
    // SZJK_SELECTED_CONTENT_QUERY
    const visible = el => !!(el && el.offsetParent !== null);
    function robustClick(el) {
      el.scrollIntoView?.({block: 'center', inline: 'center'});
      const rect = el.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(x, y);
      const target = topEl && (topEl === el || el.contains(topEl)) ? topEl : el;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y}));
      }
      el.click();
    }
    const panels = Array.from(document.querySelectorAll('div, section, form'))
      .filter(el => visible(el) && /已选内容|已选地区|已选指标/.test(el.innerText || el.textContent || ''))
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    for (const item of panels) {
      const button = Array.from(item.el.querySelectorAll('button, [role="button"], a'))
        .find(el => visible(el) && (el.innerText || el.value || el.textContent || '').trim() === '查询');
      if (button) {
        robustClick(button);
        return true;
      }
    }
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], a'))
      .filter(el => visible(el) && (el.innerText || el.value || el.textContent || '').trim() === '查询')
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .filter(item => item.rect.top > 260 && item.rect.left > 520)
      .sort((a, b) => b.rect.top - a.rect.top || a.rect.left - b.rect.left);
    const target = buttons[0]?.el;
    if (!target) return false;
    robustClick(target);
    return true;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _click_szjk_time_query(driver: object) -> bool:
    script = r"""
    // SZJK_TIME_RANGE_QUERY
    const visible = el => !!(el && el.offsetParent !== null);
    const controls = Array.from(document.querySelectorAll('div, section, form, label'))
      .filter(el => visible(el) && /按时间范围选择|按照时间范围选择/.test(el.innerText || el.textContent || ''))
      .map(el => ({el, rect: el.getBoundingClientRect()}))
      .filter(item => item.rect.top > 120 && item.rect.top < Math.max(520, window.innerHeight * 0.62))
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    for (const item of controls) {
      const rect = item.rect;
      const nearbyButtons = Array.from(document.querySelectorAll('button, [role="button"], a'))
        .filter(btn => visible(btn) && /查询/.test(btn.innerText || btn.value || btn.textContent || ''))
        .map(btn => ({btn, b: btn.getBoundingClientRect()}))
        .filter(x => Math.abs(x.b.top - rect.top) < 120 && x.b.left > rect.left && x.b.left < rect.right + 360)
        .sort((a, b) => a.b.left - b.b.left);
      const target = nearbyButtons[0]?.btn;
      if (target) {
        target.click();
        return true;
      }
    }
    const direct = Array.from(document.querySelectorAll('button, [role="button"], a'))
      .filter(btn => visible(btn) && (btn.innerText || btn.value || btn.textContent || '').trim() === '查询')
      .map(btn => ({btn, rect: btn.getBoundingClientRect()}))
      .filter(item => item.rect.top > 120 && item.rect.top < Math.max(520, window.innerHeight * 0.62))
      .sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)[0]?.btn;
    if (!direct) return false;
    direct.click();
    return true;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _wait_for_document_ready(driver: object, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ready = driver.execute_script("return document.readyState")
            if ready == "complete" or ready is True:
                return
        except Exception:
            return
        time.sleep(0.5)


def _clear_and_type(element: object, text: str) -> None:
    element.clear()
    element.send_keys(text)


def _read_page_count(html: str) -> int:
    match = re.search(r'id=["\']Count["\'][^>]*>\s*(\d+)\s*<', html)
    if not match:
        return 1
    return max(1, (int(match.group(1)) + 19) // 20)


def _wait_until_overlay_hidden(driver: object, timeout: float) -> None:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, timeout).until_not(
            EC.visibility_of_element_located((By.XPATH, "/html/body/div[1]/div[4]"))
        )
    except Exception:
        time.sleep(min(timeout, 3))
