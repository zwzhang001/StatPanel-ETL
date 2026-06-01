"""Long/wide table transformations for panel data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .io import read_table, require_columns, write_table


def long_to_wide(
    df: pd.DataFrame,
    index_columns: list[str] | None = None,
    year_column: str = "Year",
    value_column: str = "Value",
) -> pd.DataFrame:
    keys = index_columns or ["ProvinceName", "CityName", "no", "Index"]
    require_columns(df, keys + [year_column, value_column], "long panel")
    wide = df.pivot_table(index=keys, columns=year_column, values=value_column, aggfunc="first").reset_index()
    wide.columns = [str(column) for column in wide.columns]
    year_columns = sorted([column for column in wide.columns if column.isdigit()], key=int)
    return wide[keys + year_columns]


def wide_to_long(
    df: pd.DataFrame,
    id_columns: list[str] | None = None,
    year_pattern: str = r"^\d{4}$",
    value_name: str = "Value",
) -> pd.DataFrame:
    ids = id_columns or [column for column in ["ProvinceName", "CityName", "no", "Index", "Unit", "Source"] if column in df.columns]
    year_columns = [column for column in df.columns if pd.Series([str(column)]).str.match(year_pattern).iloc[0]]
    if not year_columns:
        raise ValueError("No year columns were found")
    long_df = df.melt(id_vars=ids, value_vars=year_columns, var_name="Year", value_name=value_name)
    long_df["Year"] = pd.to_numeric(long_df["Year"], errors="raise").astype(int)
    return long_df.sort_values(ids + ["Year"]).reset_index(drop=True)


def long_to_wide_file(input_file: str | Path, output_file: str | Path, index_columns: list[str] | None = None) -> Path:
    df = read_table(input_file, dtype=str)
    result = long_to_wide(df, index_columns=index_columns)
    return write_table(result, output_file)


def wide_to_long_file(input_file: str | Path, output_file: str | Path, id_columns: list[str] | None = None) -> Path:
    df = read_table(input_file, dtype=str)
    result = wide_to_long(df, id_columns=id_columns)
    return write_table(result, output_file)
