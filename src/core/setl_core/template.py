"""Panel template generation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import CITY_COLUMNS, MODEL_COLUMNS, STATUS_PENDING
from .io import read_table, require_columns, write_table
from .schemas import validate_city_list


def create_model(
    start_year: int,
    end_year: int,
    city_df: pd.DataFrame,
    indexes: list[str] | None = None,
) -> pd.DataFrame:
    if start_year > end_year:
        raise ValueError("start_year must be <= end_year")

    city_result = validate_city_list(city_df)
    city_result.raise_for_errors()
    require_columns(city_df, CITY_COLUMNS, "city list")

    years = list(range(int(start_year), int(end_year) + 1))
    index_values = indexes or [""]
    city_part = city_df[CITY_COLUMNS].copy()
    records = []
    for _, city in city_part.iterrows():
        for year in years:
            for index_name in index_values:
                records.append(
                    {
                        "ProvinceName": city["ProvinceName"],
                        "CityName": city["CityName"],
                        "no": str(city["no"]),
                        "Year": year,
                        "Index": index_name,
                        "Value": pd.NA,
                        "Unit": "",
                        "Source": "",
                        "ExtractStatus": STATUS_PENDING,
                        "MatchCount": 0,
                        "MatchedRule": "",
                        "RawFile": "",
                        "Note": "",
                    }
                )
    return pd.DataFrame.from_records(records, columns=MODEL_COLUMNS)


def create_model_file(
    start_year: int,
    end_year: int,
    city_file: str | Path,
    output_file: str | Path,
    indexes: list[str] | None = None,
) -> Path:
    city_df = read_table(city_file, dtype=str)
    model_df = create_model(start_year, end_year, city_df, indexes=indexes)
    return write_table(model_df, output_file)
