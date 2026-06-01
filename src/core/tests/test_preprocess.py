from pathlib import Path
import tempfile
import unittest

import pandas as pd

from setl_core.preprocess import merge_by_indicator, standardize_sample


class PreprocessTests(unittest.TestCase):
    def test_standardize_sample_converts_cnki_crawler_columns_for_extract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "crawler"
            output_dir = root / "standard"
            input_dir.mkdir()
            pd.DataFrame(
                {
                    "序号": ["1"],
                    "时间": ["2020年"],
                    "地区": ["中国-北京市"],
                    "指标名称": ["人均GDP"],
                    "数值": ["164889"],
                    "单位": ["元"],
                    "数据来源": ["中国城市统计年鉴 2021"],
                    "id": ["0"],
                    "ProvinceName": ["北京"],
                    "CityName": ["北京"],
                    "no": ["110100"],
                }
            ).to_csv(input_dir / "GDP_110100.csv", index=False, encoding="utf-8-sig")

            outputs = standardize_sample(input_dir, output_dir)

            self.assertEqual(outputs, [output_dir / "GDP_110100.csv"])
            result = pd.read_csv(outputs[0], dtype=str, encoding="utf-8-sig")
            self.assertEqual(list(result.columns), ["Year", "Index", "Value", "Unit", "Source", "id", "ProvinceName", "CityName", "no"])
            self.assertEqual(result.loc[0, "Year"], "2020")
            self.assertEqual(result.loc[0, "Index"], "人均GDP")
            self.assertEqual(result.loc[0, "Value"], "164889")
            self.assertEqual(result.loc[0, "Source"], "中国城市统计年鉴 2021")
            self.assertEqual(result.loc[0, "id"], "0")
            self.assertEqual(result.loc[0, "no"], "110100")
            self.assertEqual(result.loc[0, "ProvinceName"], "北京")
            self.assertEqual(result.loc[0, "CityName"], "北京")

    def test_standardize_sample_then_merge_by_indicator_batches_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "crawler"
            standard_dir = root / "standard"
            merged_dir = root / "merged"
            input_dir.mkdir()
            for city_no, city_name, value in [("110100", "北京", "164889"), ("120100", "天津", "101614")]:
                pd.DataFrame(
                    {
                        "时间": ["2020"],
                        "地区": [f"中国-{city_name}市"],
                        "指标名称": ["人均GDP"],
                        "数值": [value],
                        "单位": ["元"],
                        "数据来源": ["中国城市统计年鉴 2021"],
                        "CityName": [city_name],
                        "no": [city_no],
                    }
                ).to_csv(input_dir / f"GDP_{city_no}.csv", index=False, encoding="utf-8-sig")

            standardize_sample(input_dir, standard_dir)
            outputs = merge_by_indicator(standard_dir, merged_dir)

            self.assertEqual(outputs, [merged_dir / "GDP.csv"])
            merged = pd.read_csv(outputs[0], dtype=str, encoding="utf-8-sig")
            self.assertEqual(len(merged), 2)
            self.assertEqual(set(merged["no"]), {"110100", "120100"})
            self.assertNotIn("City_code", merged.columns)


if __name__ == "__main__":
    unittest.main()
