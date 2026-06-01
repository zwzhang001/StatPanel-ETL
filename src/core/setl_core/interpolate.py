"""Missing-value interpolation that never crosses city/index groups."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import STATUS_INTERPOLATED
from .io import read_table, require_columns, write_table


def interpolate_values(
    df: pd.DataFrame,
    method: str = "linear",
    group_columns: list[str] | None = None,
    limit_direction: str = "both",
) -> pd.DataFrame:
    groups = group_columns or ["no", "Index"]
    require_columns(df, groups + ["Year", "Value"], "extract result")
    if method not in {"linear", "ffill", "bfill", "growth"}:
        raise ValueError("method must be one of: linear, ffill, bfill, growth")

    result = df.copy()
    result["ValueOriginal"] = result["Value"]
    result["ValueFilled"] = pd.to_numeric(result["Value"], errors="coerce")
    result["FillMethod"] = ""
    result["_original_missing"] = result["ValueFilled"].isna()
    result = result.sort_values(groups + ["Year"]).reset_index(drop=True)

    filled_parts = []
    for _, group in result.groupby(groups, dropna=False, sort=False):
        part = group.copy()
        values = part["ValueFilled"]
        if method == "linear":
            filled = values.interpolate(method="linear", limit_direction=limit_direction)
        elif method == "ffill":
            filled = values.ffill()
        elif method == "bfill":
            filled = values.bfill()
        else:
            filled = values.pct_change().add(1).cumprod()
            filled = values.combine_first(filled)
            filled = filled.interpolate(method="linear", limit_direction=limit_direction)
        newly_filled = part["_original_missing"] & filled.notna()
        part["ValueFilled"] = filled
        part.loc[newly_filled, "Value"] = part.loc[newly_filled, "ValueFilled"]
        part.loc[newly_filled, "FillMethod"] = method
        if "ExtractStatus" in part.columns:
            part.loc[newly_filled, "ExtractStatus"] = STATUS_INTERPOLATED
        if "Source" in part.columns:
            part.loc[newly_filled, "Source"] = "interpolation"
        if "Note" in part.columns:
            part.loc[newly_filled, "Note"] = f"Interpolated by {method}"
        filled_parts.append(part)

    output = pd.concat(filled_parts, ignore_index=True)
    return output.drop(columns=["_original_missing"])


def interpolate_file(
    input_file: str | Path,
    output_file: str | Path,
    method: str = "linear",
    group_columns: list[str] | None = None,
    limit_direction: str = "both",
) -> Path:
    df = read_table(input_file, dtype=str)
    result = interpolate_values(df, method=method, group_columns=group_columns, limit_direction=limit_direction)
    return write_table(result, output_file)
