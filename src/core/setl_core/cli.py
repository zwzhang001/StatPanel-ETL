"""Small command-line entrypoint for core workflows."""

from __future__ import annotations

import argparse
from pathlib import Path

from .extract import ExtractRule, extract_indicator_file
from .interpolate import interpolate_file
from .preprocess import convert_encoding, merge_by_indicator, simplify_columns, standardize_sample
from .quality import export_quality_report
from .charts import plot_trend_file
from .supplement import apply_supplement_file
from .template import create_model_file
from .transform import long_to_wide_file, wide_to_long_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="setl-core", description="S-ETL core data processing commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("model", help="Create a panel model template")
    p.add_argument("--start-year", type=int, required=True)
    p.add_argument("--end-year", type=int, required=True)
    p.add_argument("--city-file", required=True)
    p.add_argument("--output-file", required=True)
    p.add_argument("--index", action="append", dest="indexes")

    p = sub.add_parser("convert-encoding", help="Convert CSV files to UTF-8")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--from-encoding", default="gb18030")

    p = sub.add_parser("merge", help="Merge city files by indicator")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)

    p = sub.add_parser("standardize-sample", help="Convert crawler sample columns to Extract-ready columns")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)

    p = sub.add_parser("simplify", help="Deprecated alias for standardize-sample")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)

    p = sub.add_parser("extract", help="Extract one indicator into a model")
    p.add_argument("--model-file", required=True)
    p.add_argument("--sample-file", required=True)
    p.add_argument("--output-file", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--unit")
    p.add_argument("--source")
    p.add_argument("--source-mode", default="any", choices=["any", "exact", "contains", "city_contains"])
    p.add_argument("--fuzzy-index", action="store_true")
    p.add_argument("--prefer", default="error", choices=["error", "first", "last", "non_null_first"])

    p = sub.add_parser("quality", help="Export a quality report")
    p.add_argument("--input-file", required=True)
    p.add_argument("--output-path", required=True)
    p.add_argument("--city-threshold", type=float, default=0.8)
    p.add_argument("--index-threshold", type=float, default=0.8)

    p = sub.add_parser("supplement", help="Apply supplement values")
    p.add_argument("--extract-file", required=True)
    p.add_argument("--supplement-file", required=True)
    p.add_argument("--output-file", required=True)
    p.add_argument("--overwrite", action="store_true")

    p = sub.add_parser("interpolate", help="Interpolate missing values")
    p.add_argument("--input-file", required=True)
    p.add_argument("--output-file", required=True)
    p.add_argument("--method", default="linear", choices=["linear", "ffill", "bfill", "growth"])

    p = sub.add_parser("long-to-wide", help="Transform long panel to wide table")
    p.add_argument("--input-file", required=True)
    p.add_argument("--output-file", required=True)

    p = sub.add_parser("wide-to-long", help="Transform wide table to long panel")
    p.add_argument("--input-file", required=True)
    p.add_argument("--output-file", required=True)

    p = sub.add_parser("trend", help="Generate a variable trend chart")
    p.add_argument("--input-file", required=True)
    p.add_argument("--output-file", required=True)
    p.add_argument("--variable", required=True)
    p.add_argument("--variable-column", default="Index")
    p.add_argument("--year-column", default="Year")
    p.add_argument("--value-column", default="Value")
    p.add_argument("--group-column")
    p.add_argument("--title")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command

    if command == "model":
        create_model_file(args.start_year, args.end_year, args.city_file, args.output_file, indexes=args.indexes)
    elif command == "convert-encoding":
        convert_encoding(args.input_dir, args.output_dir, from_encoding=args.from_encoding)
    elif command == "merge":
        merge_by_indicator(args.input_dir, args.output_dir)
    elif command == "standardize-sample":
        standardize_sample(args.input_dir, args.output_dir)
    elif command == "simplify":
        simplify_columns(args.input_dir, args.output_dir)
    elif command == "extract":
        rule = ExtractRule(
            index=args.index,
            unit=args.unit,
            source=args.source,
            source_mode=args.source_mode,
            fuzzy_index=args.fuzzy_index,
            prefer=args.prefer,
        )
        extract_indicator_file(args.model_file, args.sample_file, args.output_file, rule)
    elif command == "quality":
        export_quality_report(args.input_file, args.output_path, args.city_threshold, args.index_threshold)
    elif command == "supplement":
        apply_supplement_file(args.extract_file, args.supplement_file, args.output_file, overwrite=args.overwrite)
    elif command == "interpolate":
        interpolate_file(args.input_file, args.output_file, method=args.method)
    elif command == "long-to-wide":
        long_to_wide_file(args.input_file, args.output_file)
    elif command == "wide-to-long":
        wide_to_long_file(args.input_file, args.output_file)
    elif command == "trend":
        plot_trend_file(
            args.input_file,
            args.output_file,
            variable=args.variable,
            variable_column=args.variable_column,
            year_column=args.year_column,
            value_column=args.value_column,
            group_column=args.group_column,
            title=args.title,
        )
    else:
        raise ValueError(f"Unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
