"""Core S-ETL data processing package."""

from .charts import build_trend_data, plot_trend, plot_trend_file
from .crawler import CrawlerConfig, CrawlerResult, crawl_cnki, validate_crawler_config
from .extract import ExtractRule, extract_indicator, extract_indicator_file
from .interpolate import interpolate_values, interpolate_file
from .preprocess import convert_encoding, merge_by_indicator, simplify_columns, standardize_sample
from .quality import build_quality_report, export_quality_report
from .supplement import apply_supplement, apply_supplement_file
from .template import create_model, create_model_file
from .transform import long_to_wide, wide_to_long

__all__ = [
    "ExtractRule",
    "CrawlerConfig",
    "CrawlerResult",
    "apply_supplement",
    "apply_supplement_file",
    "build_quality_report",
    "build_trend_data",
    "convert_encoding",
    "create_model",
    "create_model_file",
    "crawl_cnki",
    "export_quality_report",
    "extract_indicator",
    "extract_indicator_file",
    "interpolate_file",
    "interpolate_values",
    "long_to_wide",
    "merge_by_indicator",
    "plot_trend",
    "plot_trend_file",
    "simplify_columns",
    "standardize_sample",
    "validate_crawler_config",
    "wide_to_long",
]
