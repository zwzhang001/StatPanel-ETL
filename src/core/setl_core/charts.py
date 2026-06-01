"""Chart helpers for quick data inspection."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import pandas as pd

from .io import ensure_parent, read_table


def _filter_variable(df: pd.DataFrame, variable: str, variable_column: str) -> pd.DataFrame:
    if variable_column not in df.columns:
        raise ValueError(f"Variable column not found: {variable_column}")
    if not variable:
        return df.copy()
    filtered = df[df[variable_column].astype(str) == str(variable)].copy()
    if filtered.empty:
        raise ValueError(f"No rows found for {variable_column}={variable}")
    return filtered


def build_trend_data(
    df: pd.DataFrame,
    variable: str,
    variable_column: str = "Index",
    year_column: str = "Year",
    value_column: str = "Value",
    group_column: str | None = None,
) -> pd.DataFrame:
    for column in [year_column, value_column]:
        if column not in df.columns:
            raise ValueError(f"Required column not found: {column}")

    data = _filter_variable(df, variable, variable_column)
    data[year_column] = pd.to_numeric(data[year_column], errors="coerce")
    data[value_column] = pd.to_numeric(data[value_column], errors="coerce")
    data = data.dropna(subset=[year_column, value_column])
    if data.empty:
        raise ValueError("No numeric trend data after filtering")

    data[year_column] = data[year_column].astype(int)
    if group_column and group_column in data.columns:
        trend = data.groupby([year_column, group_column], dropna=False)[value_column].mean().reset_index()
    else:
        trend = data.groupby(year_column, dropna=False)[value_column].mean().reset_index()
    return trend.sort_values([year_column] + ([group_column] if group_column and group_column in trend.columns else []))


def plot_trend(
    df: pd.DataFrame,
    variable: str,
    output_file: str | Path,
    variable_column: str = "Index",
    year_column: str = "Year",
    value_column: str = "Value",
    group_column: str | None = None,
    title: str | None = None,
) -> Path:
    trend = build_trend_data(
        df,
        variable=variable,
        variable_column=variable_column,
        year_column=year_column,
        value_column=value_column,
        group_column=group_column,
    )
    output_path = ensure_parent(output_file)

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(9.5, 5.2), dpi=150)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")

    if group_column and group_column in trend.columns:
        for group, part in trend.groupby(group_column, dropna=False):
            ax.plot(part[year_column], part[value_column], marker="o", linewidth=2, label=str(group))
        ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.0)
    else:
        ax.plot(trend[year_column], trend[value_column], marker="o", color="#2454a6", linewidth=2.4)

    ax.set_title(title or f"{variable or value_column} Trend", fontsize=14, pad=14, color="#18233a")
    ax.set_xlabel(year_column)
    ax.set_ylabel(value_column)
    years = sorted(trend[year_column].dropna().astype(int).unique().tolist())
    if years:
        ax.set_xticks(years)
        ax.set_xticklabels([str(year) for year in years])
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, axis="y", color="#d8dce6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#9aa7bd")
    ax.spines["bottom"].set_color("#9aa7bd")
    fig.tight_layout(rect=[0, 0, 0.82, 1] if group_column and group_column in trend.columns else None)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def plot_trend_file(
    input_file: str | Path,
    output_file: str | Path,
    variable: str,
    variable_column: str = "Index",
    year_column: str = "Year",
    value_column: str = "Value",
    group_column: str | None = None,
    title: str | None = None,
) -> Path:
    df = read_table(input_file)
    return plot_trend(
        df,
        variable=variable,
        output_file=output_file,
        variable_column=variable_column,
        year_column=year_column,
        value_column=value_column,
        group_column=group_column or None,
        title=title,
    )
