"""Apply manual supplement tables to extraction results."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import STATUS_FILLED
from .io import read_table, require_columns, write_table


def _build_key(df: pd.DataFrame, key_columns: list[str]) -> pd.Series:
    return df[key_columns].astype(str).agg("||".join, axis=1)


def apply_supplement(
    extract_df: pd.DataFrame,
    supplement_df: pd.DataFrame,
    key_columns: list[str] | None = None,
    overwrite: bool = False,
) -> pd.DataFrame:
    keys = key_columns or (["no", "Year", "Index"] if "no" in supplement_df.columns else ["CityName", "Year", "Index"])
    require_columns(extract_df, keys + ["Value"], "extract result")
    require_columns(supplement_df, keys + ["Value"], "supplement")

    result = extract_df.copy()
    supplement = supplement_df.copy()
    supplement = supplement[supplement["Value"].notna() & (supplement["Value"].astype(str).str.strip() != "")]
    if supplement.empty:
        return result

    for column in ["Source", "Unit", "Note"]:
        if column not in supplement.columns:
            supplement[column] = ""

    result["_key"] = _build_key(result, keys)
    supplement["_key"] = _build_key(supplement, keys)
    supplement = supplement.drop_duplicates("_key", keep="last").set_index("_key")

    for idx, row in result.iterrows():
        key = row["_key"]
        if key not in supplement.index:
            continue
        current_missing = pd.isna(row["Value"]) or str(row["Value"]).strip() == ""
        if not current_missing and not overwrite:
            continue
        sup = supplement.loc[key]
        result.at[idx, "Value"] = sup["Value"]
        if "Unit" in result.columns and str(sup.get("Unit", "")).strip():
            result.at[idx, "Unit"] = sup.get("Unit", "")
        if "Source" in result.columns:
            result.at[idx, "Source"] = sup.get("Source", "supplement")
        if "ExtractStatus" in result.columns:
            result.at[idx, "ExtractStatus"] = STATUS_FILLED
        if "Note" in result.columns:
            note = sup.get("Note", "")
            result.at[idx, "Note"] = note or ("Overwritten by supplement" if overwrite else "Filled by supplement")

    return result.drop(columns=["_key"])


def apply_supplement_file(
    extract_file: str | Path,
    supplement_file: str | Path,
    output_file: str | Path,
    key_columns: list[str] | None = None,
    overwrite: bool = False,
) -> Path:
    extract_df = read_table(extract_file, dtype=str)
    supplement_df = read_table(supplement_file, dtype=str)
    result = apply_supplement(extract_df, supplement_df, key_columns=key_columns, overwrite=overwrite)
    return write_table(result, output_file)
