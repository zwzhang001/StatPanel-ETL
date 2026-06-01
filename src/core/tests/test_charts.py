from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from setl_core.charts import build_trend_data, plot_trend


class ChartTests(unittest.TestCase):
    def test_build_trend_data_groups_by_requested_column(self):
        df = pd.DataFrame(
            {
                "Index": ["人均GDP", "人均GDP", "人均GDP", "人均GDP"],
                "Year": ["2019", "2020", "2019", "2020"],
                "Value": ["10", "12", "20", "22"],
                "CityName": ["北京", "北京", "天津", "天津"],
            }
        )

        trend = build_trend_data(df, "人均GDP", group_column="CityName")

        self.assertEqual(list(trend.columns), ["Year", "CityName", "Value"])
        self.assertEqual(trend["Year"].tolist(), [2019, 2019, 2020, 2020])
        self.assertEqual(set(trend["CityName"]), {"北京", "天津"})

    def test_plot_trend_accepts_chinese_title(self):
        df = pd.DataFrame(
            {
                "Index": ["人均GDP", "人均GDP"],
                "Year": ["2019", "2020"],
                "Value": ["164220", "164889"],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "trend.png"
            result = plot_trend(df, "人均GDP", output, title="人均GDP趋势")

            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_plot_trend_places_group_legend_outside_right(self):
        df = pd.DataFrame(
            {
                "Index": ["GDP", "GDP", "GDP", "GDP"],
                "Year": ["2019", "2020", "2019", "2020"],
                "Value": ["10", "12", "20", "22"],
                "CityName": ["Beijing", "Beijing", "Tianjin", "Tianjin"],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch("matplotlib.axes.Axes.legend") as legend:
            plot_trend(df, "GDP", Path(tmpdir) / "trend.png", group_column="CityName")

            _, kwargs = legend.call_args
            self.assertEqual(kwargs["loc"], "center left")
            self.assertEqual(kwargs["bbox_to_anchor"], (1.02, 0.5))


if __name__ == "__main__":
    unittest.main()
