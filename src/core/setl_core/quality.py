"""Quality reports for extraction results."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .io import read_table, require_columns, write_table


def _missing_mask(df: pd.DataFrame) -> pd.Series:
    return df["Value"].isna() | (df["Value"].astype(str).str.strip() == "")


def _rate_table(df: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    temp = df.copy()
    temp["_missing"] = _missing_mask(temp)
    grouped = temp.groupby(group_columns, dropna=False)["_missing"]
    result = grouped.agg(total="count", missing="sum").reset_index()
    result["missing_rate"] = result["missing"] / result["total"]
    return result


def build_quality_report(
    df: pd.DataFrame,
    city_threshold: float = 0.8,
    index_threshold: float = 0.8,
) -> dict[str, pd.DataFrame]:
    require_columns(df, ["no", "Year", "Index", "Value"], "extract result")
    missing = _missing_mask(df)
    summary = pd.DataFrame(
        [
            {
                "total_rows": len(df),
                "missing_rows": int(missing.sum()),
                "missing_rate": float(missing.mean()) if len(df) else 0.0,
            }
        ]
    )
    by_city = _rate_table(df, ["no", "CityName"] if "CityName" in df.columns else ["no"])
    by_year = _rate_table(df, ["Year"])
    by_index = _rate_table(df, ["Index"])
    missing_rows = df[missing].copy()

    delete_city = by_city[by_city["missing_rate"] >= city_threshold].copy()
    delete_index = by_index[by_index["missing_rate"] >= index_threshold].copy()
    supplement_needed = missing_rows.copy()

    return {
        "summary": summary,
        "by_city": by_city,
        "by_year": by_year,
        "by_index": by_index,
        "missing_rows": missing_rows,
        "delete_city_suggestions": delete_city,
        "delete_index_suggestions": delete_index,
        "supplement_needed": supplement_needed,
    }


def export_quality_report(
    input_file: str | Path,
    output_path: str | Path,
    city_threshold: float = 0.8,
    index_threshold: float = 0.8,
) -> Path:
    df = read_table(input_file)
    report = build_quality_report(df, city_threshold=city_threshold, index_threshold=index_threshold)
    output = Path(output_path)
    if output.suffix.lower() in {".xlsx", ".xls"}:
        output.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(output) as writer:
            for sheet_name, table in report.items():
                table.to_excel(writer, sheet_name=sheet_name[:31], index=False)
        return output

    output.mkdir(parents=True, exist_ok=True)
    for name, table in report.items():
        write_table(table, output / f"{name}.csv")
    return output
