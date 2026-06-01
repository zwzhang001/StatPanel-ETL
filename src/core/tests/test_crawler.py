from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from setl_core.crawler import (
    CNKI_INDICATOR_SEARCH_URL,
    CNKI_STAT_SEARCH_URL,
    CrawlerConfig,
    _fetch_city_rows,
    _fetch_szjk_rows,
    _filter_rows_by_year,
    _fill_szjk_indexs_indicator_name,
    _fill_szjk_indexs_region,
    _fill_szjk_indexs_time,
    _fill_szjk_indexs_form,
    _fill_and_verify_szjk_indexs_form,
    _fetch_szjk_indexs_rows,
    _read_szjk_indexs_form_state,
    _build_szjk_indexs_payload,
    _records_to_szjk_indexs_rows,
    _records_to_original_table_rows,
    _find_region_node,
    _build_szjk_field_json,
    _submit_szjk_indexs_query_via_vue,
    _html_has_year_headers,
    _fill_szjk_time_range,
    _click_szjk_indexs_query,
    _click_szjk_time_query,
    _click_szjk_selected_content_query,
    _clear_szjk_selected_content,
    _read_indicator_candidates_from_driver,
    _select_szjk_indicator,
    build_output_path,
    crawl_cnki,
    extract_unit_from_index,
    indicator_search_keyword,
    normalize_crawler_rows,
    parse_cnki_rows,
    parse_indicator_candidates,
    parse_szjk_rows,
    read_city_rows,
    search_cnki_indicators,
    validate_crawler_config,
)


class CrawlerConfigTests(unittest.TestCase):
    def test_default_urls_use_current_cnki_stat_search_center(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,Beijing\n", encoding="utf-8")
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020)
            self.assertEqual(config.login_url, CNKI_INDICATOR_SEARCH_URL)
            self.assertEqual(config.search_url, CNKI_STAT_SEARCH_URL)
            self.assertIn("data-easysearchReplace", config.login_url)
            self.assertIn("data-indexs", config.search_url)

    def test_validate_requires_keyword_city_file_and_valid_years(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,Beijing\n", encoding="utf-8")
            validate_crawler_config(CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020))
            with self.assertRaises(ValueError):
                validate_crawler_config(CrawlerConfig(keyword="", city_file=city_file, start_year=2000, end_year=2020))
            with self.assertRaises(ValueError):
                validate_crawler_config(CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2021, end_year=2020))


class CrawlerParsingTests(unittest.TestCase):
    def test_parse_cnki_rows_extracts_standard_columns(self):
        html = """
        <table>
          <tr><th></th><th>No</th><th>Year</th><th>Region</th><th>Index</th><th>Value</th><th>Unit</th><th>Source</th><th>PageNumber</th></tr>
          <tr><td></td><td>1</td><td>2020</td><td>Beijing</td><td>GDP</td><td>100</td><td>billion yuan</td><td>Yearbook</td><td>12</td></tr>
        </table>
        """
        rows = parse_cnki_rows(html)
        self.assertEqual(rows[0]["Value"], "100")
        self.assertEqual(rows[0]["PageNumber"], "12")

    def test_read_city_rows_accepts_cityname_and_no_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            pd.DataFrame({"ProvinceName": ["Beijing"], "CityName": ["Beijing"], "no": ["110100"]}).to_csv(city_file, index=False)
            self.assertEqual(
                read_city_rows(city_file),
                [
                    {
                        "no": "110100",
                        "name": "Beijing",
                        "_city_fields": {"ProvinceName": "Beijing", "CityName": "Beijing", "no": "110100"},
                    }
                ],
            )

    def test_build_output_path_uses_keyword_and_city_no(self):
        self.assertEqual(build_output_path(Path("out"), "GDP", "110100"), Path("out") / "GDP_110100.csv")

    def test_parse_szjk_rows_maps_statistical_table_to_standard_columns(self):
        html = """
        <table>
          <tr><th>Region</th><th>Year</th><th>Index</th><th>Value</th><th>Unit</th></tr>
          <tr><td>Beijing</td><td>2020</td><td>GDP(100 million yuan)</td><td>100</td><td>100 million yuan</td></tr>
        </table>
        """
        self.assertEqual(
            parse_szjk_rows(html, "Beijing", "GDP"),
            [{"No": "", "Year": "2020", "Region": "Beijing", "Index": "GDP(100 million yuan)", "Value": "100", "Unit": "100 million yuan"}],
        )

    def test_parse_szjk_rows_maps_data_indexs_result_table(self):
        html = """
        <table>
          <tr><th>\u5e8f\u53f7</th><th>\u65f6\u95f4</th><th>\u5730\u533a</th><th>\u6307\u6807\u540d\u79f0</th><th>\u6570\u503c</th><th>\u5355\u4f4d</th><th>\u6570\u636e\u6765\u6e90</th></tr>
          <tr><td>1</td><td>2020\u5e74</td><td>\u4e2d\u56fd-\u5317\u4eac\u5e02</td><td>\u4eba\u5747GDP</td><td>164889</td><td>\u5143</td><td>\u4e2d\u56fd\u57ce\u5e02\u7edf\u8ba1\u5e74\u9274 2021</td></tr>
        </table>
        """
        self.assertEqual(
            parse_szjk_rows(html, "\u5317\u4eac\u5e02", "\u4eba\u5747GDP"),
            [{"No": "", "Year": "2020", "Region": "\u4e2d\u56fd-\u5317\u4eac\u5e02", "Index": "\u4eba\u5747GDP", "Value": "164889", "Unit": "\u5143"}],
        )

    def test_parse_szjk_rows_expands_year_columns_from_result_table(self):
        html = """
        <table>
          <tr><th>指标名称(单位)</th><th>地区</th><th>2020年</th><th>2019年</th></tr>
          <tr><td>人均GDP(元)</td><td>北京市</td><td>164889.0</td><td>164220.0</td></tr>
        </table>
        """

        self.assertEqual(
            parse_szjk_rows(html, "北京市", "人均GDP"),
            [
                {"No": "", "Year": "2020", "Region": "北京市", "Index": "人均GDP(元)", "Value": "164889.0", "Unit": "元"},
                {"No": "", "Year": "2019", "Region": "北京市", "Index": "人均GDP(元)", "Value": "164220.0", "Unit": "元"},
            ],
        )

    def test_indicator_candidates_keep_parentheses_content(self):
        html = """
        <label><input type="checkbox" />GDP(市辖区)(万元)</label>
        <label><input type="checkbox" />GDP(亿元)</label>
        """
        self.assertEqual(parse_indicator_candidates(html, "GDP"), ["GDP(市辖区)(万元)", "GDP(亿元)"])

    def test_indicator_candidates_are_filtered_by_keyword(self):
        html = """
        <label><input type="checkbox" />Second industry employees(person)</label>
        <label><input type="checkbox" />Second industry urban employees(10,000 persons)</label>
        <label><input type="checkbox" />GDP(100 million yuan)</label>
        """
        self.assertEqual(
            parse_indicator_candidates(html, "Second industry"),
            ["Second industry employees(person)", "Second industry urban employees(10,000 persons)"],
        )

    def test_extract_unit_from_last_parentheses(self):
        self.assertEqual(extract_unit_from_index("Second industry share(city district)(%)"), "%")
        self.assertEqual(extract_unit_from_index("GDP"), "")

    def test_indicator_search_keyword_removes_last_unit_parentheses(self):
        self.assertEqual(indicator_search_keyword("人均 GDP (元)"), "人均GDP")
        self.assertEqual(indicator_search_keyword("GDP(市辖区)(万元)"), "GDP(市辖区)")
        self.assertEqual(indicator_search_keyword("Second industry employees(person)"), "Second industry employees")

    def test_normalize_rows_uses_city_file_and_exact_indicator_schema(self):
        rows = normalize_crawler_rows(
            [{"Year": "2020", "Value": "100"}],
            city={"no": "110100", "name": "Beijing"},
            exact_index="GDP(100 million yuan)",
        )
        self.assertEqual(rows, [{"No": "110100", "Region": "Beijing", "Year": "2020", "Index": "GDP(100 million yuan)", "Value": "100", "Unit": "100 million yuan"}])

    def test_filter_rows_by_year_keeps_only_requested_range(self):
        rows = [
            {"Year": "2023年", "Value": "300"},
            {"Year": "2020年", "Value": "200"},
            {"Year": "2019", "Value": "100"},
            {"Year": "2018", "Value": "50"},
        ]
        self.assertEqual(
            _filter_rows_by_year(rows, 2019, 2020),
            [{"Year": "2020", "Value": "200"}, {"Year": "2019", "Value": "100"}],
        )


class CrawlerProgressTests(unittest.TestCase):
    def test_crawl_cnki_emits_preview_event_after_each_saved_city_file(self):
        class FakeDriver:
            def get(self, url): self.url = url
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            city_file = tmp_path / "cities.csv"
            pd.DataFrame({"ProvinceName": ["Beijing"], "CityName": ["Beijing"], "no": ["110100"]}).to_csv(city_file, index=False)
            events = []
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020, output_dir=tmp_path / "out", login_wait_seconds=0, selected_indexes=["GDP(100 million yuan)"])
            with patch("setl_core.crawler._fetch_city_rows", return_value=[{"\u5e8f\u53f7": "1", "\u65f6\u95f4": "2020", "\u5730\u533a": "Beijing", "\u6307\u6807\u540d\u79f0": "GDP", "\u6570\u503c": "100", "\u5355\u4f4d": "100 million yuan", "\u6570\u636e\u6765\u6e90": "Yearbook"}]):
                crawl_cnki(config, driver_factory=lambda _: FakeDriver(), preview=events.append)
            saved = pd.read_csv(events[0]["path"], dtype=str, encoding="gb18030")
            self.assertEqual(list(saved.columns), ["\u5e8f\u53f7", "\u65f6\u95f4", "\u5730\u533a", "\u6307\u6807\u540d\u79f0", "\u6570\u503c", "\u5355\u4f4d", "\u6570\u636e\u6765\u6e90", "ProvinceName", "CityName", "no"])
            self.assertEqual(saved.loc[0, "\u65f6\u95f4"], "2020")
            self.assertEqual(saved.loc[0, "\u6307\u6807\u540d\u79f0"], "GDP")
            self.assertEqual(saved.loc[0, "CityName"], "Beijing")

    def test_crawl_cnki_stops_when_cancel_callback_requests_stop(self):
        class FakeDriver:
            def get(self, url): self.url = url
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            city_file = tmp_path / "cities.csv"
            pd.DataFrame({"no": ["110100", "120100"], "CityName": ["Beijing", "Tianjin"]}).to_csv(city_file, index=False)
            calls = []
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020, output_dir=tmp_path / "out", login_wait_seconds=0, selected_indexes=["GDP(100 million yuan)"])

            def fake_fetch(driver, config, city_name, log, **kwargs):
                calls.append(city_name)
                return [{"Year": "2020", "Value": "100"}]

            with patch("setl_core.crawler._fetch_city_rows", side_effect=fake_fetch):
                crawl_cnki(config, driver_factory=lambda _: FakeDriver(), cancel_requested=lambda: bool(calls))
            self.assertEqual(calls, ["Beijing"])

    def test_crawl_cnki_stops_after_browser_session_is_lost(self):
        class FakeDriver:
            def get(self, url): self.url = url
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            city_file = tmp_path / "cities.csv"
            pd.DataFrame({"no": ["110100", "120100"], "CityName": ["Beijing", "Tianjin"]}).to_csv(city_file, index=False)
            calls = []
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020, output_dir=tmp_path / "out", login_wait_seconds=0, selected_indexes=["GDP(100 million yuan)"])

            def fail_once(driver, config, city_name, log, **kwargs):
                calls.append(city_name)
                raise RuntimeError("Message: Tried to run command without establishing a connection")

            with patch("setl_core.crawler._fetch_city_rows", side_effect=fail_once):
                result = crawl_cnki(config, driver_factory=lambda _: FakeDriver())
            self.assertEqual(calls, ["Beijing"])
            self.assertIn("Browser session was lost", result.failed_items[0]["error"])


class CrawlerRoutingTests(unittest.TestCase):
    def test_fetch_city_rows_routes_szjk_url_to_new_search_handler(self):
        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2000, end_year=2020, search_url=CNKI_STAT_SEARCH_URL)
        with patch("setl_core.crawler._fetch_szjk_rows", return_value=[{"Value": "100"}]) as fetch_szjk:
            self.assertEqual(_fetch_city_rows(object(), config, "Beijing", lambda message: None), [{"Value": "100"}])
        fetch_szjk.assert_called_once()

    def test_fetch_city_rows_routes_current_data_easysearch_url_to_new_search_handler(self):
        config = CrawlerConfig(
            keyword="GDP",
            city_file=Path("cities.csv"),
            start_year=2000,
            end_year=2020,
            search_url="https://szjk.cnki.net/dpi/search-center/#/data-easysearch?dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1",
        )
        with patch("setl_core.crawler._fetch_szjk_rows", return_value=[{"Value": "100"}]) as fetch_szjk:
            self.assertEqual(_fetch_city_rows(object(), config, "Beijing", lambda message: None), [{"Value": "100"}])
        fetch_szjk.assert_called_once()

    def test_fetch_city_rows_routes_current_data_indexs_url_to_new_search_handler(self):
        config = CrawlerConfig(
            keyword="GDP",
            city_file=Path("cities.csv"),
            start_year=2000,
            end_year=2020,
            search_url="https://szjk.cnki.net/dpi/search-center/#/data-indexs?dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1&system=1",
        )
        with patch("setl_core.crawler._fetch_szjk_rows", return_value=[{"Value": "100"}]) as fetch_szjk:
            self.assertEqual(_fetch_city_rows(object(), config, "Beijing", lambda message: None), [{"Value": "100"}])
        fetch_szjk.assert_called_once()

    def test_search_cnki_indicators_returns_keyword_candidates_from_page(self):
        class FakeDriver:
            page_source = '<label><input type="checkbox" />Second industry employees(person)</label><label><input type="checkbox" />GDP(100 million yuan)</label>'
            def get(self, url): self.url = url
            def execute_script(self, *args): return True
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,Beijing\n", encoding="utf-8")
            config = CrawlerConfig(keyword="Second industry", city_file=city_file, start_year=2000, end_year=2020, login_wait_seconds=0)
            self.assertEqual(search_cnki_indicators(config, driver_factory=lambda _: FakeDriver()), ["Second industry employees(person)"])

    def test_search_cnki_indicators_defaults_to_easysearchreplace_page(self):
        class FakeDriver:
            page_source = '<label><input type="checkbox" />GDP(100 million yuan)</label>'
            def get(self, url): self.url = url
            def execute_script(self, *args): return True
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,Beijing\n", encoding="utf-8")
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020, login_wait_seconds=0)
            driver = FakeDriver()
            with patch("setl_core.crawler._fill_szjk_indexs_indicator_name") as fill_indexs:
                self.assertEqual(search_cnki_indicators(config, driver_factory=lambda _: driver), ["GDP(100 million yuan)"])
            self.assertEqual(driver.url, CNKI_INDICATOR_SEARCH_URL)
            fill_indexs.assert_not_called()

    def test_search_cnki_indicators_prefers_browser_dom_full_text(self):
        class FakeDriver:
            page_source = ""
            def get(self, url): self.url = url
            def execute_script(self, script, *args):
                if "INDICATOR_CANDIDATE_EXTRACTOR" in script:
                    return ["GDP(市辖区)(万元)", "GDP(亿元)"]
                return True
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,Beijing\n", encoding="utf-8")
            config = CrawlerConfig(keyword="GDP", city_file=city_file, start_year=2000, end_year=2020, login_wait_seconds=0)
            self.assertEqual(search_cnki_indicators(config, driver_factory=lambda _: FakeDriver()), ["GDP(市辖区)(万元)", "GDP(亿元)"])

    def test_search_cnki_indicators_on_data_indexs_only_requires_indicator_name(self):
        class FakeDriver:
            page_source = '<table><tr><th>\u65f6\u95f4</th><th>\u5730\u533a</th><th>\u6307\u6807\u540d\u79f0</th><th>\u6570\u503c</th><th>\u5355\u4f4d</th></tr><tr><td>2020\u5e74</td><td>\u4e2d\u56fd-\u5317\u4eac\u5e02</td><td>\u4eba\u5747GDP</td><td>164889</td><td>\u5143</td></tr></table>'
            def get(self, url): self.url = url
            def execute_script(self, *args): return True
            def quit(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            city_file = Path(tmpdir) / "cities.csv"
            city_file.write_text("no,CityName\n110100,\u5317\u4eac\u5e02\n", encoding="utf-8")
            config = CrawlerConfig(
                keyword="\u4eba\u5747GDP",
                city_file=city_file,
                start_year=2019,
                end_year=2020,
                login_url=CNKI_STAT_SEARCH_URL,
                login_wait_seconds=0,
            )
            with patch("setl_core.crawler._fill_szjk_indexs_indicator_name", return_value=True) as fill_name, \
                 patch("setl_core.crawler._fill_szjk_indexs_form", return_value=False) as full_form, \
                 patch("setl_core.crawler._click_szjk_indexs_query", return_value=True):
                self.assertEqual(search_cnki_indicators(config, driver_factory=lambda _: FakeDriver()), ["\u4eba\u5747GDP(\u5143)"])
        fill_name.assert_called_once()
        full_form.assert_not_called()

    def test_indicator_candidates_read_title_full_name_with_unit(self):
        class FakeDriver:
            def execute_script(self, script, keyword):
                self.script = script
                self.keyword = keyword
                return ["人均GDP", "人均GDP(元)"]

        driver = FakeDriver()
        self.assertEqual(_read_indicator_candidates_from_driver(driver, "人均GDP"), ["人均GDP", "人均GDP(元)"])
        self.assertIn("title", driver.script)

    def test_fetch_szjk_rows_does_not_fallback_to_stale_table_when_exact_indicator_missing(self):
        class FakeDriver:
            page_source = "<table><tr><th>指标</th><th>2023年</th></tr><tr><td>常住人口城镇化率(%)</td><td>87.83</td></tr></table>"
            def get(self, url): self.url = url
            def execute_script(self, *args): return True

        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2019, end_year=2020, selected_indexes=["人均GDP(元)"])
        config.search_url = "https://szjk.cnki.net/dpi/search-center/#/data-easysearch?dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1"
        with patch("setl_core.crawler._open_szjk_data_analysis", return_value=True), \
             patch("setl_core.crawler._clear_szjk_selected_content", return_value=True), \
             patch("setl_core.crawler._fill_szjk_field", return_value=True), \
             patch("setl_core.crawler._search_szjk_indicator_tree", return_value=True), \
             patch("setl_core.crawler._select_szjk_indicator", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Exact indicator checkbox"):
                _fetch_szjk_rows(FakeDriver(), config, "北京", lambda message: None)

    def test_fetch_szjk_rows_queries_before_and_after_time_range_then_filters_years(self):
        class FakeDriver:
            page_source = """
            <table>
              <tr><th>指标名称(单位)</th><th>地区</th><th>2023年</th><th>2020年</th><th>2019年</th></tr>
              <tr><td>人均GDP(元)</td><td>北京</td><td>999</td><td>200</td><td>100</td></tr>
            </table>
            """
            def get(self, url): self.url = url
            def execute_script(self, *args): return True

        calls = []
        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2019, end_year=2020, selected_indexes=["人均GDP(元)"])
        config.search_url = "https://szjk.cnki.net/dpi/search-center/#/data-easysearch?dimensionId=595b3bf46c0b44469ae0d38f531cd789&type=1"
        with patch("setl_core.crawler._open_szjk_data_analysis", return_value=True), \
             patch("setl_core.crawler._clear_szjk_selected_content", return_value=True), \
             patch("setl_core.crawler._open_szjk_left_tab", return_value=True), \
             patch("setl_core.crawler._select_szjk_region", return_value=True), \
             patch("setl_core.crawler._search_szjk_indicator_tree", return_value=True), \
             patch("setl_core.crawler._select_szjk_indicator", return_value=True), \
             patch("setl_core.crawler._fill_szjk_time_range", side_effect=lambda *args: calls.append("time") or True), \
             patch("setl_core.crawler._click_szjk_selected_content_query", side_effect=lambda *args: calls.append("selected_query") or True), \
             patch("setl_core.crawler._click_szjk_time_query", side_effect=lambda *args: calls.append("time_query") or True):
            rows = _fetch_szjk_rows(FakeDriver(), config, "北京", lambda message: None)

        self.assertEqual(calls, ["selected_query", "time", "time_query"])
        self.assertEqual([row["Year"] for row in rows], ["2020", "2019"])

    def test_records_to_original_table_rows_preserves_cnki_table_fields_only(self):
        records = [
            {
                "date": "2020\u5e74",
                "area": "\u4e2d\u56fd-\u77f3\u5bb6\u5e84\u5e02",
                "indexName": "\u4eba\u5747GDP",
                "value": "95589",
                "unit": "\u5143",
                "data_source": "\u4e2d\u56fd\u57ce\u5e02\u7edf\u8ba1\u5e74\u9274",
                "njnf": "2021",
                "area_code": "130100",
                "tb_indexes_id": "idx-1",
            }
        ]
        self.assertEqual(
            _records_to_original_table_rows(records),
            [
                {
                    "\u5e8f\u53f7": "1",
                    "\u65f6\u95f4": "2020",
                    "\u5730\u533a": "\u4e2d\u56fd-\u77f3\u5bb6\u5e84\u5e02",
                    "\u6307\u6807\u540d\u79f0": "\u4eba\u5747GDP",
                    "\u6570\u503c": "95589",
                    "\u5355\u4f4d": "\u5143",
                    "\u6570\u636e\u6765\u6e90": "\u4e2d\u56fd\u57ce\u5e02\u7edf\u8ba1\u5e74\u9274 2021",
                }
            ],
        )

    def test_fetch_szjk_rows_uses_data_indexs_vue_controls_per_city(self):
        class FakeDriver:
            page_source = ""
            def get(self, url): self.url = url
            def execute_script(self, *args): return {}

        vue_calls = []
        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2019, end_year=2020, selected_indexes=["\u4eba\u5747GDP(\u5143)"])
        vue_result = {
            "ready": True,
            "summary": "ok",
            "rows": [
                {"date": "2020\u5e74", "area": "\u4e2d\u56fd-\u5317\u4eac\u5e02", "indexName": "\u4eba\u5747GDP", "value": "164889", "unit": "\u5143"},
                {"date": "2019\u5e74", "area": "\u4e2d\u56fd-\u5317\u4eac\u5e02", "indexName": "\u4eba\u5747GDP", "value": "164220", "unit": "\u5143"},
            ],
        }
        with patch("setl_core.crawler._resolve_cnki_indicator_selection", return_value={"name": "\u4eba\u5747GDP(\u5143)", "query_name": "\u4eba\u5747GDP", "code": "idx-1"}), \
             patch("setl_core.crawler._submit_szjk_indexs_query_via_vue", side_effect=lambda *args, **kwargs: vue_calls.append(args[1:6]) or vue_result), \
             patch("setl_core.crawler._cnki_api_request_json") as api_request, \
             patch("setl_core.crawler._fill_and_verify_szjk_indexs_form") as fill_form:
            rows = _fetch_szjk_rows(FakeDriver(), config, {"name": "\u5317\u4eac\u5e02", "no": "110100"}, lambda message: None)

        self.assertEqual(vue_calls[0], ({"name": "\u4eba\u5747GDP(\u5143)", "query_name": "\u4eba\u5747GDP", "code": "idx-1"}, "\u5317\u4eac\u5e02", "110100", 2019, 2020))
        api_request.assert_not_called()
        fill_form.assert_not_called()
        self.assertEqual(list(rows[0].keys()), ["\u5e8f\u53f7", "\u65f6\u95f4", "\u5730\u533a", "\u6307\u6807\u540d\u79f0", "\u6570\u503c", "\u5355\u4f4d", "\u6570\u636e\u6765\u6e90"])
        self.assertEqual([row["\u65f6\u95f4"] for row in rows], ["2020", "2019"])
        self.assertEqual(rows[0]["\u6307\u6807\u540d\u79f0"], "\u4eba\u5747GDP")

    def test_fetch_szjk_rows_retries_when_vue_returns_stale_city_rows(self):
        class FakeDriver:
            page_source = ""
            def get(self, url): self.url = url
            def execute_script(self, *args): return {}

        stale_result = {
            "ready": True,
            "summary": "stale",
            "rows": [
                {"date": "2020\u5e74", "area": "\u4e2d\u56fd-\u5f20\u5bb6\u53e3\u5e02", "area_code": "130700", "indexName": "\u4eba\u5747GDP", "value": "1", "unit": "\u5143"},
            ],
        }
        current_result = {
            "ready": True,
            "summary": "current",
            "rows": [
                {"date": "2020\u5e74", "area": "\u4e2d\u56fd-\u627f\u5fb7\u5e02", "area_code": "130800", "indexName": "\u4eba\u5747GDP", "value": "2", "unit": "\u5143"},
            ],
        }
        logs = []
        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2020, end_year=2020, selected_indexes=["\u4eba\u5747GDP(\u5143)"])
        with patch("setl_core.crawler._resolve_cnki_indicator_selection", return_value={"name": "\u4eba\u5747GDP(\u5143)", "query_name": "\u4eba\u5747GDP", "code": "idx-1"}), \
             patch("setl_core.crawler._submit_szjk_indexs_query_via_vue", side_effect=[stale_result, current_result]) as submit:
            rows = _fetch_szjk_rows(FakeDriver(), config, {"name": "\u627f\u5fb7", "no": "130800"}, logs.append)

        self.assertEqual(submit.call_count, 2)
        self.assertEqual(rows[0]["\u5730\u533a"], "\u4e2d\u56fd-\u627f\u5fb7\u5e02")
        self.assertEqual(rows[0]["\u6570\u503c"], "2")
        self.assertTrue(any("stale data-indexs rows" in message for message in logs))

    def test_fetch_szjk_rows_retries_when_vue_controls_are_not_ready_first(self):
        class FakeDriver:
            page_source = ""
            def get(self, url): self.url = url
            def execute_script(self, *args): return {}

        not_ready = {
            "ready": False,
            "summary": 'indexIds=["old"]; areaCodes=["110000"]; dates=["2019","2020"]; rows=2',
        }
        ready = {
            "ready": True,
            "summary": 'indexIds=["idx-1"]; areaCodes=["110000"]; dates=["2019","2020"]; rows=2',
            "rows": [
                {"date": "2020\u5e74", "area": "\u4e2d\u56fd-\u5317\u4eac\u5e02", "area_code": "110000", "indexName": "\u4eba\u5747GDP", "value": "164889", "unit": "\u5143"},
            ],
        }
        logs = []
        config = CrawlerConfig(keyword="GDP", city_file=Path("cities.csv"), start_year=2020, end_year=2020, selected_indexes=["\u4eba\u5747GDP(\u5143)"])
        with patch("setl_core.crawler._resolve_cnki_indicator_selection", return_value={"name": "\u4eba\u5747GDP(\u5143)", "query_name": "\u4eba\u5747GDP", "code": "idx-1"}), \
             patch("setl_core.crawler._submit_szjk_indexs_query_via_vue", side_effect=[not_ready, ready]) as submit:
            rows = _fetch_szjk_rows(FakeDriver(), config, {"name": "\u5317\u4eac", "no": "110100"}, logs.append)

        self.assertEqual(submit.call_count, 2)
        self.assertEqual(rows[0]["\u5730\u533a"], "\u4e2d\u56fd-\u5317\u4eac\u5e02")
        self.assertTrue(any("controls were not ready" in message for message in logs))

    def test_submit_szjk_indexs_query_via_vue_uses_page_component_methods(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_async_script(self, script, *args):
                self.calls.append((script, args))
                return {"ready": True, "rows": [], "summary": "ok"}

        driver = FakeDriver()
        result = _submit_szjk_indexs_query_via_vue(
            driver,
            {"name": "\u4eba\u5747GDP(\u5143)", "query_name": "\u4eba\u5747GDP", "code": "idx-1"},
            "\u5317\u4eac",
            "110100",
            2019,
            2020,
            timeout=1,
        )
        script = driver.calls[0][0]
        self.assertTrue(result["ready"])
        self.assertIn("treeSelect", script)
        self.assertIn("handleRadioChange", script)
        self.assertIn("timeChange", script)
        self.assertIn("handleSearch", script)
        self.assertIn("cloneTableRows", script)
        self.assertIn("dataIndex", script)
        self.assertIn("forceSelectedIndex", script)

    def test_fetch_szjk_indexs_rows_rejects_stale_result_rows_for_other_query(self):
        records = [
            {"date": "2020\u5e74", "area": "\u4e2d\u56fd-\u5929\u6d25\u5e02", "indexName": "\u666e\u901a\u5c0f\u5b66\u5b66\u6821\u6570", "value": "934", "unit": "\u6240"},
            {"date": "2019\u5e74", "area": "\u4e2d\u56fd-\u5929\u6d25\u5e02", "indexName": "\u666e\u901a\u5c0f\u5b66\u5b66\u6821\u6570", "value": "941", "unit": "\u6240"},
        ]
        rows = _records_to_szjk_indexs_rows(records, "\u4fdd\u5b9a", "\u4eba\u5747GDP", 2019, 2020)
        self.assertEqual(rows, [])

    def test_build_szjk_indexs_payload_uses_frontend_variable_names(self):
        payload = _build_szjk_indexs_payload(
            indicator={"name": "\u4eba\u5747GDP(\u5143)", "code": "idx-1"},
            region={"regionName": "\u5317\u4eac\u5e02", "regionCode": "110100", "type": "city"},
            start_year=2019,
            end_year=2020,
            dimension_id="dim-1",
            field_json={"\u53ef\u9009\u7ef4\u5ea6": ""},
        )
        self.assertEqual(payload["indexIds"], ["idx-1"])
        self.assertEqual(payload["areaCodes"], ["110100"])
        self.assertEqual(payload["type"], "city")
        self.assertEqual(payload["dates"], ["2019", "2020"])
        self.assertEqual(payload["timeType"], "1")
        self.assertEqual(payload["timeFrequency"], "year")
        self.assertEqual(payload["timeFrequencies"], ["\u5e74\u5ea6"])
        self.assertEqual(payload["json"], '{"\u53ef\u9009\u7ef4\u5ea6": ""}')

    def test_find_region_node_reads_cnki_sys_region_list_and_children2(self):
        result = {
            "sysRegionList": [
                {
                    "regionName": "\u4e2d\u56fd",
                    "regionCode": "100000",
                    "children2": [
                        {
                            "regionName": "\u5317\u4eac\u5e02",
                            "regionCode": "110100",
                            "type": "2",
                        }
                    ],
                }
            ]
        }
        self.assertEqual(
            _find_region_node(result, "\u5317\u4eac", "110100"),
            {"regionName": "\u5317\u4eac\u5e02", "regionCode": "110100", "type": "2"},
        )

    def test_build_szjk_field_json_matches_frontend_dynamic_dimension_filter(self):
        dimensions = [
            {"description": "\u6307\u6807\u540d\u79f0", "filedName": "indexName", "jswdFlag": "zbjs,"},
            {"description": "\u53ef\u9009\u7ef4\u5ea6A", "filedName": "a", "jswdFlag": "zbjs,"},
            {"description": "\u53ef\u9009\u7ef4\u5ea6B", "filedName": "b", "jswdFlag": "zbjs,"},
            {"description": "\u5355\u4f4d", "filedName": "unit", "jswdFlag": "zbjsjgzs,"},
        ]
        self.assertEqual(_build_szjk_field_json(dimensions), {"\u53ef\u9009\u7ef4\u5ea6A": "", "\u53ef\u9009\u7ef4\u5ea6B": ""})

    def test_fill_szjk_indexs_form_targets_data_indexs_controls(self):
        calls = []
        with patch("setl_core.crawler._fill_szjk_indexs_indicator_name", side_effect=lambda *args: calls.append("index") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_region", side_effect=lambda *args: calls.append("region") or True), \
             patch("setl_core.crawler._select_szjk_indexs_annual_frequency", side_effect=lambda *args: calls.append("annual") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_time", side_effect=lambda *args: calls.append("time") or True):
            self.assertTrue(_fill_szjk_indexs_form(object(), "\u4eba\u5747GDP", "\u5317\u4eac\u5e02", 2019, 2020))
        self.assertEqual(calls, ["index", "region", "annual", "time"])

    def test_fill_and_verify_szjk_indexs_form_refills_only_failed_state_parts(self):
        calls = []
        states = [
            {"ready": False, "has_indicator": True, "has_region": True, "has_time": False, "summary": "time=2006\u5e74-2026\u5e74"},
            {"ready": True, "has_indicator": True, "has_region": True, "has_time": True, "summary": "ok"},
        ]
        with patch("setl_core.crawler._fill_szjk_indexs_indicator_name", side_effect=lambda *args: calls.append("index") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_region", side_effect=lambda *args: calls.append("region") or True), \
             patch("setl_core.crawler._select_szjk_indexs_annual_frequency", side_effect=lambda *args: calls.append("annual") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_time", side_effect=lambda *args: calls.append("time") or True), \
             patch("setl_core.crawler._read_szjk_indexs_form_state", side_effect=states):
            state = _fill_and_verify_szjk_indexs_form(object(), "\u4eba\u5747GDP", "\u5317\u4eac\u5e02", 2019, 2020, timeout=1)
        self.assertTrue(state["ready"])
        self.assertEqual(calls, ["index", "region", "annual", "time", "annual", "time"])

    def test_fill_and_verify_szjk_indexs_form_waits_after_initial_controls_are_missing(self):
        calls = []
        states = [
            {"ready": False, "has_indicator": False, "has_region": False, "has_time": False, "summary": "controls missing"},
            {"ready": True, "has_indicator": True, "has_region": True, "has_time": True, "summary": "ok"},
        ]
        with patch("setl_core.crawler._fill_szjk_indexs_form", side_effect=lambda *args: calls.append("full") or False), \
             patch("setl_core.crawler._fill_szjk_indexs_indicator_name", side_effect=lambda *args: calls.append("index") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_region", side_effect=lambda *args: calls.append("region") or True), \
             patch("setl_core.crawler._select_szjk_indexs_annual_frequency", side_effect=lambda *args: calls.append("annual") or True), \
             patch("setl_core.crawler._fill_szjk_indexs_time", side_effect=lambda *args: calls.append("time") or True), \
             patch("setl_core.crawler._read_szjk_indexs_form_state", side_effect=states):
            state = _fill_and_verify_szjk_indexs_form(object(), "\u4eba\u5747GDP", "\u5317\u4eac\u5e02", 2019, 2020, timeout=1)
        self.assertTrue(state["ready"])
        self.assertEqual(calls, ["full", "index", "region", "annual", "time"])

    def test_fill_szjk_indexs_indicator_name_targets_only_indicator_name_control(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_fill_szjk_indexs_indicator_name(driver, "\u4eba\u5747GDP"))
        self.assertEqual(driver.calls[0][1], ("\u4eba\u5747GDP",))
        self.assertIn("SZJK_DATA_INDEXS_INDICATOR_NAME", driver.calls[0][0])
        self.assertIn("\\u6307\\u6807\\u540d\\u79f0", driver.calls[0][0])

    def test_fill_szjk_indexs_region_uses_dropdown_option(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_fill_szjk_indexs_region(driver, "\u5317\u4eac\u5e02", wait_seconds=0))
        scripts = "\n".join(call[0] for call in driver.calls)
        self.assertIn("SZJK_DATA_INDEXS_REGION_OPEN", scripts)
        self.assertIn("SZJK_DATA_INDEXS_REGION_SELECT", scripts)
        self.assertGreaterEqual(len(driver.calls), 2)

    def test_fill_szjk_indexs_time_sets_popup_year_pair(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_fill_szjk_indexs_time(driver, 2019, 2020, wait_seconds=0))
        scripts = "\n".join(call[0] for call in driver.calls)
        self.assertIn("SZJK_DATA_INDEXS_TIME_OPEN", scripts)
        self.assertIn("SZJK_DATA_INDEXS_TIME_SET", scripts)

    def test_read_szjk_indexs_form_state_treats_open_region_dropdown_as_unready(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return {"ready": False, "has_region": False, "summary": "region dropdown is still open"}

        driver = FakeDriver()
        state = _read_szjk_indexs_form_state(driver, "\u4eba\u5747GDP", "\u4fdd\u5b9a", 2019, 2020)
        self.assertFalse(state["ready"])
        self.assertIn("regionPopupOpen", driver.calls[0][0])
        self.assertIn("isTextInput", driver.calls[0][0])

    def test_click_szjk_indexs_query_targets_top_form_query_button(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_click_szjk_indexs_query(driver))
        self.assertIn("SZJK_DATA_INDEXS_QUERY", driver.calls[0][0])
        self.assertIn("\\u67e5\\u8be2", driver.calls[0][0])

    def test_fill_szjk_time_range_uses_start_and_end_year_value(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_fill_szjk_time_range(driver, 2019, 2020))
        self.assertEqual(driver.calls[0][1][0], "2019年-2020年")
        self.assertEqual(driver.calls[0][1][1], 2019)
        self.assertEqual(driver.calls[0][1][2], 2020)
        self.assertIn("按时间范围选择", driver.calls[0][0])
        self.assertIn("按照时间范围选择", driver.calls[0][0])
        self.assertIn("已选时间", driver.calls[0][0])

    def test_click_szjk_time_query_targets_time_range_query_button(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_click_szjk_time_query(driver))
        self.assertIn("SZJK_TIME_RANGE_QUERY", driver.calls[0][0])
        self.assertIn("按时间范围选择", driver.calls[0][0])
        self.assertIn("查询", driver.calls[0][0])

    def test_click_szjk_selected_content_query_targets_selected_content_dialog(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_click_szjk_selected_content_query(driver))
        self.assertIn("SZJK_SELECTED_CONTENT_QUERY", driver.calls[0][0])
        self.assertIn("已选内容", driver.calls[0][0])
        self.assertIn("已选地区", driver.calls[0][0])

    def test_clear_szjk_selected_content_clears_existing_page_selection(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, *args):
                self.calls.append((script, args))
                return True

        driver = FakeDriver()
        self.assertTrue(_clear_szjk_selected_content(driver))
        self.assertIn("SZJK_CLEAR_SELECTED_CONTENT", driver.calls[0][0])
        self.assertIn("全部清除", driver.calls[0][0])

    def test_select_szjk_indicator_clicks_exact_checkbox_label(self):
        class FakeDriver:
            def __init__(self): self.calls = []
            def execute_script(self, script, value):
                self.calls.append((script, value))
                return True

        driver = FakeDriver()
        self.assertTrue(_select_szjk_indicator(driver, "人均GDP(元)"))
        self.assertEqual(driver.calls[0][1], "人均GDP(元)")
        self.assertIn("INDICATOR_EXACT_SELECTOR", driver.calls[0][0])


if __name__ == "__main__":
    unittest.main()
