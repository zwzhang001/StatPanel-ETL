"""Shared constants for S-ETL core modules."""

ENCODING = "utf-8-sig"

CITY_COLUMNS = ["ProvinceName", "CityName", "no"]

MODEL_COLUMNS = [
    "ProvinceName",
    "CityName",
    "no",
    "Year",
    "Index",
    "Value",
    "Unit",
    "Source",
    "ExtractStatus",
    "MatchCount",
    "MatchedRule",
    "RawFile",
    "Note",
]

RAW_SAMPLE_COLUMNS = [
    "No",
    "Year",
    "Region",
    "Index",
    "Value",
    "Unit",
    "Source",
    "PageNumber",
]

PREPROCESSED_COLUMNS = [
    "Year",
    "Index",
    "Value",
    "Unit",
    "Source",
    "id",
    "ProvinceName",
    "CityName",
    "no",
]

STATUS_PENDING = "pending"
STATUS_MATCHED = "matched"
STATUS_MISSING = "missing"
STATUS_CONFLICT = "conflict"
STATUS_FILLED = "filled"
STATUS_INTERPOLATED = "interpolated"

EXTRACT_STATUSES = {
    STATUS_PENDING,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_CONFLICT,
    STATUS_FILLED,
    STATUS_INTERPOLATED,
}
