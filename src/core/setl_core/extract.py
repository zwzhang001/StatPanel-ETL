"""Indicator extraction from preprocessed samples into panel templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import MODEL_COLUMNS, STATUS_CONFLICT, STATUS_MATCHED, STATUS_MISSING
from .io import read_table, require_columns, write_table


@dataclass
class ExtractRule:
    index: str
    unit: str | None = None
    source: str | None = None
    source_mode: str = "any"
    fuzzy_index: bool = False
    prefer: str = "error"
    name: str = ""

    def rule_name(self) -> str:
        return self.name or f"index={self.index};unit={self.unit or '*'};source={self.source_mode}:{self.source or '*'}"


def _normalize_year(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.extract(r"(\d{4}|\d+)", expand=False), errors="coerce").astype("Int64")


def _ensure_no_column(sample_df: pd.DataFrame) -> pd.DataFrame:
    sample = sample_df.copy()
    if "no" not in sample.columns and "City_code" in sample.columns:
        sample["no"] = sample["City_code"]
    return sample


def _filter_candidates(sample_df: pd.DataFrame, row: pd.Series, rule: ExtractRule) -> pd.DataFrame:
    candidates = sample_df.copy()
    if rule.fuzzy_index:
        candidates = candidates[candidates["Index"].astype(str).str.contains(rule.index, na=False, regex=False)]
    else:
        candidates = candidates[candidates["Index"].astype(str) == str(rule.index)]

    if rule.unit:
        candidates = candidates[candidates["Unit"].astype(str) == str(rule.unit)]

    if "no" in candidates.columns:
        candidates = candidates[candidates["no"].astype(str) == str(row["no"])]
    elif "Region" in candidates.columns:
        candidates = candidates[candidates["Region"].astype(str) == str(row["CityName"])]

    candidates = candidates[candidates["Year"] == int(row["Year"])]

    if rule.source_mode == "exact" and rule.source:
        candidates = candidates[candidates["Source"].astype(str) == str(rule.source)]
    elif rule.source_mode == "contains" and rule.source:
        candidates = candidates[candidates["Source"].astype(str).str.contains(rule.source, na=False, regex=False)]
    elif rule.source_mode == "city_contains":
        candidates = candidates[candidates["Source"].astype(str).str.contains(str(row["CityName"]), na=False, regex=False)]
    elif rule.source_mode == "any":
        pass
    else:
        raise ValueError(f"Unsupported source_mode: {rule.source_mode}")
    return candidates


def _select_candidate(candidates: pd.DataFrame, prefer: str) -> pd.Series | None:
    if candidates.empty:
        return None
    if len(candidates) == 1:
        return candidates.iloc[0]
    if prefer == "first":
        return candidates.iloc[0]
    if prefer == "last":
        return candidates.iloc[-1]
    if prefer == "non_null_first":
        non_null = candidates[candidates["Value"].notna()]
        return non_null.iloc[0] if not non_null.empty else candidates.iloc[0]
    return None


def extract_indicator(model_df: pd.DataFrame, sample_df: pd.DataFrame, rule: ExtractRule) -> pd.DataFrame:
    require_columns(model_df, ["ProvinceName", "CityName", "no", "Year"], "model")
    require_columns(sample_df, ["Year", "Index", "Value", "Unit", "Source"], "sample")

    model = model_df.copy()
    sample = _ensure_no_column(sample_df)
    sample["Year"] = _normalize_year(sample["Year"])
    model["Year"] = pd.to_numeric(model["Year"], errors="raise").astype(int)

    if "Index" not in model.columns:
        model["Index"] = rule.index
    else:
        model["Index"] = model["Index"].replace("", pd.NA).fillna(rule.index)

    for column in MODEL_COLUMNS:
        if column not in model.columns:
            if column == "MatchCount":
                model[column] = 0
            else:
                model[column] = ""

    for idx, row in model.iterrows():
        candidates = _filter_candidates(sample, row, rule)
        match_count = len(candidates)
        selected = _select_candidate(candidates, rule.prefer)
        model.at[idx, "MatchCount"] = str(match_count)
        model.at[idx, "MatchedRule"] = rule.rule_name()
        if selected is None and match_count == 0:
            model.at[idx, "ExtractStatus"] = STATUS_MISSING
            model.at[idx, "Note"] = "No matching sample row"
            continue
        if selected is None and match_count > 1:
            model.at[idx, "ExtractStatus"] = STATUS_CONFLICT
            model.at[idx, "Note"] = f"{match_count} matching rows; set prefer to first/last/non_null_first to choose"
            continue

        model.at[idx, "Value"] = selected.get("Value", pd.NA)
        model.at[idx, "Unit"] = selected.get("Unit", rule.unit or "")
        model.at[idx, "Source"] = selected.get("Source", "")
        model.at[idx, "ExtractStatus"] = STATUS_MATCHED if match_count == 1 else STATUS_CONFLICT
        model.at[idx, "RawFile"] = selected.get("RawFile", "")
        model.at[idx, "Note"] = "" if match_count == 1 else f"Selected by prefer={rule.prefer} from {match_count} rows"

    return model[MODEL_COLUMNS]


def extract_indicator_file(
    model_file: str | Path,
    sample_file: str | Path,
    output_file: str | Path,
    rule: ExtractRule,
) -> Path:
    model_df = read_table(model_file, dtype=str)
    sample_df = read_table(sample_file, dtype=str)
    result = extract_indicator(model_df, sample_df, rule)
    return write_table(result, output_file)
