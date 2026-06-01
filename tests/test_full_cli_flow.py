from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "Step3Core"
WORK_DIR = ROOT / "Step5Test" / "full_cli_flow_workspace"


def run_cli(*args: str) -> None:
    command = [sys.executable, "-m", "setl_core.cli", *args]
    subprocess.run(command, cwd=CORE_DIR, check=True)


def reset_workspace() -> None:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    for name in [
        "raw",
        "utf8",
        "merged",
        "simple",
        "model",
        "extract",
        "quality",
        "supplement",
        "interpolate",
        "transform",
        "load",
    ]:
        (WORK_DIR / name).mkdir(parents=True, exist_ok=True)


def write_inputs() -> None:
    city_df = pd.DataFrame(
        [
            {"ProvinceName": "ProvinceA", "CityName": "Alpha", "no": "1001"},
            {"ProvinceName": "ProvinceB", "CityName": "Beta", "no": "1002"},
        ]
    )
    city_df.to_csv(WORK_DIR / "city_list.csv", index=False, encoding="utf-8")

    raw_columns = ["No", "Year", "Region", "Index", "Value", "Unit", "Source", "PageNumber"]
    alpha = pd.DataFrame(
        [
            ["1", "2020", "Alpha", "GDP", "100", "billion yuan", "Yearbook Alpha", "10"],
            ["2", "2022", "Alpha", "GDP", "140", "billion yuan", "Yearbook Alpha", "12"],
        ],
        columns=raw_columns,
    )
    beta = pd.DataFrame(
        [
            ["1", "2020", "Beta", "GDP", "200", "billion yuan", "Yearbook Beta", "20"],
            ["2", "2021", "Beta", "GDP", "220", "billion yuan", "Yearbook Beta", "21"],
            ["3", "2022", "Beta", "GDP", "240", "billion yuan", "Yearbook Beta", "22"],
        ],
        columns=raw_columns,
    )
    alpha.to_csv(WORK_DIR / "raw" / "GDP_1001.csv", index=False, encoding="gb18030")
    beta.to_csv(WORK_DIR / "raw" / "GDP_1002.csv", index=False, encoding="gb18030")

    supplement = pd.DataFrame(
        [
            {
                "no": "1001",
                "Year": "2021",
                "Index": "GDP",
                "Value": "120",
                "Unit": "billion yuan",
                "Source": "Manual supplement",
                "Note": "User supplied missing Alpha 2021 value",
            }
        ]
    )
    supplement.to_csv(WORK_DIR / "supplement.csv", index=False, encoding="utf-8")


def assert_outputs() -> None:
    paths = {
        "model": WORK_DIR / "model" / "model.csv",
        "simple": WORK_DIR / "simple" / "GDP.csv",
        "extract": WORK_DIR / "extract" / "GDP_extract.csv",
        "quality": WORK_DIR / "quality" / "quality_report.xlsx",
        "supplement": WORK_DIR / "supplement" / "GDP_supplemented.csv",
        "interpolate": WORK_DIR / "interpolate" / "GDP_interpolated.csv",
        "wide": WORK_DIR / "transform" / "GDP_wide.csv",
        "long": WORK_DIR / "transform" / "GDP_long.csv",
        "chart": WORK_DIR / "load" / "GDP_trend.png",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    assert not missing, f"Missing expected outputs: {missing}"
    assert paths["chart"].stat().st_size > 0, "Trend chart was created but is empty"

    extracted = pd.read_csv(paths["extract"], dtype=str)
    assert len(extracted) == 6, f"Expected 6 extracted rows, got {len(extracted)}"
    alpha_2021 = extracted[(extracted["no"] == "1001") & (extracted["Year"] == "2021")]
    assert alpha_2021.iloc[0]["ExtractStatus"] == "missing", "Alpha 2021 should be missing before supplement"

    supplemented = pd.read_csv(paths["supplement"], dtype=str)
    alpha_2021_sup = supplemented[(supplemented["no"] == "1001") & (supplemented["Year"] == "2021")]
    assert alpha_2021_sup.iloc[0]["Value"] == "120", "Supplement value was not applied"

    interpolated = pd.read_csv(paths["interpolate"], dtype=str)
    assert "ValueFilled" in interpolated.columns, "Interpolation audit column ValueFilled is missing"

    wide = pd.read_csv(paths["wide"], dtype=str)
    assert {"2020", "2021", "2022"}.issubset(set(wide.columns)), "Wide output does not contain year columns"

    long_df = pd.read_csv(paths["long"], dtype=str)
    assert len(long_df) == 6, f"Expected 6 rows after wide-to-long, got {len(long_df)}"


def main() -> None:
    reset_workspace()
    write_inputs()

    run_cli(
        "convert-encoding",
        "--input-dir",
        str(WORK_DIR / "raw"),
        "--output-dir",
        str(WORK_DIR / "utf8"),
        "--from-encoding",
        "gb18030",
    )
    run_cli("merge", "--input-dir", str(WORK_DIR / "utf8"), "--output-dir", str(WORK_DIR / "merged"))
    run_cli("simplify", "--input-dir", str(WORK_DIR / "merged"), "--output-dir", str(WORK_DIR / "simple"))
    run_cli(
        "model",
        "--start-year",
        "2020",
        "--end-year",
        "2022",
        "--city-file",
        str(WORK_DIR / "city_list.csv"),
        "--output-file",
        str(WORK_DIR / "model" / "model.csv"),
        "--index",
        "GDP",
    )
    run_cli(
        "extract",
        "--model-file",
        str(WORK_DIR / "model" / "model.csv"),
        "--sample-file",
        str(WORK_DIR / "simple" / "GDP.csv"),
        "--output-file",
        str(WORK_DIR / "extract" / "GDP_extract.csv"),
        "--index",
        "GDP",
        "--unit",
        "billion yuan",
    )
    run_cli(
        "quality",
        "--input-file",
        str(WORK_DIR / "extract" / "GDP_extract.csv"),
        "--output-path",
        str(WORK_DIR / "quality" / "quality_report.xlsx"),
    )
    run_cli(
        "supplement",
        "--extract-file",
        str(WORK_DIR / "extract" / "GDP_extract.csv"),
        "--supplement-file",
        str(WORK_DIR / "supplement.csv"),
        "--output-file",
        str(WORK_DIR / "supplement" / "GDP_supplemented.csv"),
    )
    run_cli(
        "interpolate",
        "--input-file",
        str(WORK_DIR / "supplement" / "GDP_supplemented.csv"),
        "--output-file",
        str(WORK_DIR / "interpolate" / "GDP_interpolated.csv"),
    )
    run_cli(
        "long-to-wide",
        "--input-file",
        str(WORK_DIR / "interpolate" / "GDP_interpolated.csv"),
        "--output-file",
        str(WORK_DIR / "transform" / "GDP_wide.csv"),
    )
    run_cli(
        "wide-to-long",
        "--input-file",
        str(WORK_DIR / "transform" / "GDP_wide.csv"),
        "--output-file",
        str(WORK_DIR / "transform" / "GDP_long.csv"),
    )
    run_cli(
        "trend",
        "--input-file",
        str(WORK_DIR / "interpolate" / "GDP_interpolated.csv"),
        "--output-file",
        str(WORK_DIR / "load" / "GDP_trend.png"),
        "--variable",
        "GDP",
        "--group-column",
        "CityName",
    )

    assert_outputs()
    print(f"Full CLI flow passed. Outputs: {WORK_DIR}")


if __name__ == "__main__":
    main()
