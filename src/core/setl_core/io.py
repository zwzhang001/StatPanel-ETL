"""I/O helpers for CSV and Excel files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import ENCODING


def ensure_parent(path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def read_table(path: str | Path, encoding: str = ENCODING, dtype: str | None = None) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path, dtype=dtype)
    return pd.read_csv(file_path, encoding=encoding, dtype=dtype)


def write_table(df: pd.DataFrame, path: str | Path, encoding: str = ENCODING, index: bool = False) -> Path:
    output_path = ensure_parent(path)
    suffix = output_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df.to_excel(output_path, index=index)
    else:
        df.to_csv(output_path, encoding=encoding, index=index)
    return output_path


def csv_files(input_dir: str | Path) -> list[Path]:
    return sorted(Path(input_dir).glob("*.csv"))


def drop_unnamed_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed")]


def require_columns(df: pd.DataFrame, columns: Iterable[str], table_name: str = "table") -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{table_name} is missing required columns: {joined}")
