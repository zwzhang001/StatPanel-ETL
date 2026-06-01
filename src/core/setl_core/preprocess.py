"""Preprocessing utilities for old CNKI/yearbook CSV outputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import ENCODING, PREPROCESSED_COLUMNS
from .io import csv_files, drop_unnamed_columns, read_table, write_table

CNKI_SAMPLE_COLUMN_MAP = {
    "\u65f6\u95f4": "Year",
    "\u5e74\u4efd": "Year",
    "\u5730\u533a": "Region",
    "\u6307\u6807\u540d\u79f0": "Index",
    "\u6307\u6807": "Index",
    "\u6570\u503c": "Value",
    "\u503c": "Value",
    "\u5355\u4f4d": "Unit",
    "\u6570\u636e\u6765\u6e90": "Source",
    "\u6765\u6e90": "Source",
}


def convert_encoding(input_dir: str | Path, output_dir: str | Path, from_encoding: str = "gb18030") -> list[Path]:
    outputs: list[Path] = []
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for source_file in csv_files(input_dir):
        df = pd.read_csv(source_file, encoding=from_encoding)
        outputs.append(write_table(df, Path(output_dir) / source_file.name, encoding=ENCODING))
    return outputs


def _split_indicator_city(file_path: Path) -> tuple[str, str]:
    stem = file_path.stem
    if "_" not in stem:
        return stem, ""
    indicator, city_code = stem.rsplit("_", 1)
    return indicator, city_code


def merge_by_indicator(input_dir: str | Path, output_dir: str | Path) -> list[Path]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[pd.DataFrame]] = {}
    for source_file in csv_files(input_dir):
        indicator, city_code = _split_indicator_city(source_file)
        df = read_table(source_file)
        df = drop_unnamed_columns(df)
        if "City_code" not in df.columns and "no" not in df.columns:
            df["City_code"] = city_code
        groups.setdefault(indicator, []).append(df)

    outputs: list[Path] = []
    for indicator, frames in groups.items():
        merged = pd.concat(frames, ignore_index=True)
        outputs.append(write_table(merged, Path(output_dir) / f"{indicator}.csv"))
    return outputs


def standardize_sample(
    input_dir: str | Path,
    output_dir: str | Path,
    keep_columns: list[str] | None = None,
) -> list[Path]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    keep = keep_columns or PREPROCESSED_COLUMNS
    outputs: list[Path] = []
    for source_file in csv_files(input_dir):
        _indicator, city_code = _split_indicator_city(source_file)
        df = read_table(source_file)
        df = drop_unnamed_columns(df)
        df = _rename_cnki_sample_columns(df)
        if "Year" in df.columns:
            df["Year"] = df["Year"].astype(str).str.extract(r"((?:19|20)?\d{2})", expand=False).fillna(df["Year"].astype(str))
        if "City_code" not in df.columns and city_code:
            df["City_code"] = city_code
        if "no" not in df.columns and "City_code" in df.columns:
            df["no"] = df["City_code"]
        if "City_code" not in df.columns and "no" in df.columns:
            df["City_code"] = df["no"]
        existing = [column for column in keep if column in df.columns]
        simplified = df[existing].copy()
        outputs.append(write_table(simplified, Path(output_dir) / source_file.name))
    return outputs


def simplify_columns(
    input_dir: str | Path,
    output_dir: str | Path,
    keep_columns: list[str] | None = None,
) -> list[Path]:
    return standardize_sample(input_dir, output_dir, keep_columns=keep_columns)


def _rename_cnki_sample_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {column: CNKI_SAMPLE_COLUMN_MAP[column] for column in df.columns if column in CNKI_SAMPLE_COLUMN_MAP}
    return df.rename(columns=rename_map)
