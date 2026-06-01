"""Validation helpers based on the Step2 data contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import CITY_COLUMNS, EXTRACT_STATUSES, MODEL_COLUMNS, RAW_SAMPLE_COLUMNS


@dataclass
class ValidationIssue:
    level: str
    code: str
    message: str
    row: int | None = None
    column: str | None = None


@dataclass
class ValidationResult:
    table_name: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    def add(self, level: str, code: str, message: str, row: int | None = None, column: str | None = None) -> None:
        self.issues.append(ValidationIssue(level, code, message, row, column))

    def raise_for_errors(self) -> None:
        if self.is_valid:
            return
        messages = [f"{issue.code}: {issue.message}" for issue in self.issues if issue.level == "error"]
        raise ValueError("; ".join(messages))


def _check_required(df: pd.DataFrame, required: list[str], result: ValidationResult) -> None:
    for column in required:
        if column not in df.columns:
            result.add("error", "missing_column", f"Missing required column {column}", column=column)


def _check_integer_column(df: pd.DataFrame, column: str, result: ValidationResult) -> None:
    if column not in df.columns:
        return
    parsed = pd.to_numeric(df[column], errors="coerce")
    bad = df[column].notna() & parsed.isna()
    for row in df.index[bad].tolist()[:20]:
        result.add("error", "invalid_integer", f"{column} must be an integer", int(row), column)


def _check_number_column(df: pd.DataFrame, column: str, result: ValidationResult) -> None:
    if column not in df.columns:
        return
    parsed = pd.to_numeric(df[column], errors="coerce")
    bad = df[column].notna() & (df[column].astype(str).str.strip() != "") & parsed.isna()
    for row in df.index[bad].tolist()[:20]:
        result.add("error", "invalid_number", f"{column} must be numeric or empty", int(row), column)


def validate_city_list(df: pd.DataFrame) -> ValidationResult:
    result = ValidationResult("city_list")
    _check_required(df, CITY_COLUMNS, result)
    if "no" in df.columns:
        empty = df["no"].isna() | (df["no"].astype(str).str.strip() == "")
        if empty.any():
            result.add("error", "empty_key", "Column no cannot be empty", column="no")
        duplicated = df["no"].astype(str).duplicated(keep=False)
        if duplicated.any():
            result.add("error", "duplicate_key", "Column no must be unique", column="no")
    return result


def validate_raw_sample(df: pd.DataFrame) -> ValidationResult:
    result = ValidationResult("raw_sample")
    _check_required(df, RAW_SAMPLE_COLUMNS, result)
    _check_integer_column(df, "Year", result)
    return result


def validate_preprocessed_sample(df: pd.DataFrame, city_df: pd.DataFrame | None = None) -> ValidationResult:
    result = ValidationResult("preprocessed_sample")
    _check_required(df, ["Year", "Region", "Index", "Value", "Unit", "Source"], result)
    if "no" not in df.columns and "City_code" not in df.columns:
        result.add("error", "missing_city_key", "Expected no or City_code")
    _check_integer_column(df, "Year", result)
    _check_number_column(df, "Value", result)
    if city_df is not None and "no" in df.columns and "no" in city_df.columns:
        known = set(city_df["no"].astype(str))
        unknown = ~df["no"].astype(str).isin(known)
        unknown = unknown & df["no"].notna() & (df["no"].astype(str).str.strip() != "")
        if unknown.any():
            result.add("error", "unknown_city", "Some no values are not present in city list", column="no")
    return result


def validate_extract_result(df: pd.DataFrame, city_df: pd.DataFrame | None = None) -> ValidationResult:
    result = ValidationResult("extract_result")
    _check_required(df, MODEL_COLUMNS, result)
    _check_integer_column(df, "Year", result)
    _check_number_column(df, "Value", result)
    if {"no", "Year", "Index"}.issubset(df.columns):
        duplicated = df[["no", "Year", "Index"]].astype(str).duplicated(keep=False)
        if duplicated.any():
            result.add("error", "duplicate_key", "no + Year + Index must be unique")
    if "ExtractStatus" in df.columns:
        invalid = ~df["ExtractStatus"].isin(EXTRACT_STATUSES)
        if invalid.any():
            result.add("error", "invalid_status", "ExtractStatus has values outside the allowed enum")
    if "MatchCount" in df.columns:
        _check_integer_column(df, "MatchCount", result)
        negative = pd.to_numeric(df["MatchCount"], errors="coerce") < 0
        if negative.any():
            result.add("error", "negative_match_count", "MatchCount must be >= 0", column="MatchCount")
    if city_df is not None and "no" in df.columns and "no" in city_df.columns:
        known = set(city_df["no"].astype(str))
        unknown = ~df["no"].astype(str).isin(known)
        if unknown.any():
            result.add("error", "unknown_city", "Some no values are not present in city list", column="no")
    return result


def load_contract_path() -> Path:
    return Path(__file__).resolve().parents[2] / "Step2DefineDataFramework" / "schemas.json"
