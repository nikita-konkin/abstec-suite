from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FEATURE_NAMES = [
    "1",
    "e",
    "t",
    "c",
    "e2",
    "t2",
    "c2",
    "e_t",
    "e_c",
    "t_c",
]


@dataclass
class Row:
    site: str
    elevation: float
    timestep: float
    coefficient: float
    iv_non_zero_count: float
    iv_max: float
    dcb_entry_count: float


@dataclass
class ModelResult:
    target: str
    coefficients: list[float]
    r2: float
    mae: float
    rmse: float
    sample_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract an interpretable polynomial model from absolTEC experiment CSVs"
    )
    parser.add_argument(
        "--input-csv",
        default="experiments/absoltec_experiments_success_only_2026_001.csv",
        help="Input CSV produced by experiment runner",
    )
    parser.add_argument(
        "--output-report",
        default="experiments/absoltec_model_report_2026_001.md",
        help="Output markdown report path",
    )
    parser.add_argument(
        "--only-positive-quality",
        action="store_true",
        help="Use only rows where iv_non_zero_count>0 and dcb_entry_count>0",
    )
    return parser.parse_args()


def build_features(elevation: float, timestep: float, coefficient: float) -> list[float]:
    e = elevation
    t = timestep
    c = coefficient
    return [
        1.0,
        e,
        t,
        c,
        e * e,
        t * t,
        c * c,
        e * t,
        e * c,
        t * c,
    ]


def transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(col) for col in zip(*matrix)]


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    result = [[0.0 for _ in range(len(b[0]))] for _ in range(len(a))]
    for i in range(len(a)):
        for k in range(len(b)):
            aik = a[i][k]
            for j in range(len(b[0])):
                result[i][j] += aik * b[k][j]
    return result


def matvec(a: list[list[float]], x: list[float]) -> list[float]:
    return [sum(a[i][j] * x[j] for j in range(len(x))) for i in range(len(a))]


def solve_linear_system(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(a)
    augmented = [row[:] + [b[i]] for i, row in enumerate(a)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < 1e-12:
            raise ValueError("Singular matrix while solving regression system")

        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]
        pivot = augmented[col][col]

        for j in range(col, n + 1):
            augmented[col][j] /= pivot

        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            for j in range(col, n + 1):
                augmented[row][j] -= factor * augmented[col][j]

    return [augmented[i][n] for i in range(n)]


def fit_polynomial_model(x_rows: list[list[float]], y_values: list[float], ridge: float = 1e-8) -> list[float]:
    xt = transpose(x_rows)
    xtx = matmul(xt, x_rows)
    xty = [sum(xt[i][k] * y_values[k] for k in range(len(y_values))) for i in range(len(xt))]

    for i in range(len(xtx)):
        xtx[i][i] += ridge

    return solve_linear_system(xtx, xty)


def metrics(y_true: list[float], y_pred: list[float]) -> tuple[float, float, float]:
    if not y_true:
        return 0.0, 0.0, 0.0

    n = len(y_true)
    mean_y = sum(y_true) / n
    ss_tot = sum((value - mean_y) ** 2 for value in y_true)
    ss_res = sum((y_true[i] - y_pred[i]) ** 2 for i in range(n))

    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 1.0
    mae = sum(abs(y_true[i] - y_pred[i]) for i in range(n)) / n
    rmse = (ss_res / n) ** 0.5
    return r2, mae, rmse


def load_rows(csv_path: Path, only_positive_quality: bool) -> list[Row]:
    rows: list[Row] = []
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for record in reader:
            if record.get("status", "") != "ok":
                continue

            row = Row(
                site=record.get("site", "unknown"),
                elevation=float(record["elevation_cutoff"]),
                timestep=float(record["time_step_hours"]),
                coefficient=float(record["correction_coefficient"]),
                iv_non_zero_count=float(record["iv_non_zero_count"]),
                iv_max=float(record["iv_max"]),
                dcb_entry_count=float(record["dcb_entry_count"]),
            )

            if only_positive_quality and not (
                row.iv_non_zero_count > 0 and row.dcb_entry_count > 0
            ):
                continue

            rows.append(row)

    if not rows:
        raise ValueError("No usable rows found in input CSV with current filter settings")
    return rows


def fit_targets(rows: list[Row]) -> list[ModelResult]:
    x_rows = [build_features(r.elevation, r.timestep, r.coefficient) for r in rows]
    targets = {
        "iv_non_zero_count": [r.iv_non_zero_count for r in rows],
        "iv_max": [r.iv_max for r in rows],
        "dcb_entry_count": [r.dcb_entry_count for r in rows],
    }

    results: list[ModelResult] = []
    for target_name, y_values in targets.items():
        coef = fit_polynomial_model(x_rows, y_values)
        predictions = [sum(coef[i] * x_row[i] for i in range(len(coef))) for x_row in x_rows]
        r2, mae, rmse = metrics(y_values, predictions)
        results.append(
            ModelResult(
                target=target_name,
                coefficients=coef,
                r2=r2,
                mae=mae,
                rmse=rmse,
                sample_count=len(rows),
            )
        )

    return results


def format_equation(coefficients: list[float]) -> str:
    parts: list[str] = []
    for index, coefficient in enumerate(coefficients):
        term = FEATURE_NAMES[index]
        if index == 0:
            parts.append(f"{coefficient:.6f}")
        else:
            sign = "+" if coefficient >= 0 else "-"
            parts.append(f" {sign} {abs(coefficient):.6f}*{term}")
    return "".join(parts)


def site_summary(rows: list[Row]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        summary[row.site] = summary.get(row.site, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: item[0]))


def write_report(output_path: Path, input_csv: Path, rows: list[Row], results: list[ModelResult]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    site_counts = site_summary(rows)

    lines: list[str] = []
    lines.append("# absolTEC model extraction report")
    lines.append("")
    lines.append(f"Input CSV: {input_csv}")
    lines.append(f"Samples used: {len(rows)}")
    lines.append("")
    lines.append("## Samples per site")
    lines.append("")
    for site, count in site_counts.items():
        lines.append(f"- {site}: {count}")
    lines.append("")
    lines.append("## Feature basis")
    lines.append("")
    lines.append("f = [1, e, t, c, e2, t2, c2, e_t, e_c, t_c]")
    lines.append("where e=elevation_cutoff, t=time_step_hours, c=correction_coefficient")
    lines.append("")

    for result in results:
        lines.append(f"## Target: {result.target}")
        lines.append("")
        lines.append(f"- R2: {result.r2:.6f}")
        lines.append(f"- MAE: {result.mae:.6f}")
        lines.append(f"- RMSE: {result.rmse:.6f}")
        lines.append(f"- Equation: {result.target} = {format_equation(result.coefficients)}")
        lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_csv = Path(args.input_csv).resolve()
    output_report = Path(args.output_report).resolve()

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    rows = load_rows(input_csv, only_positive_quality=args.only_positive_quality)
    results = fit_targets(rows)
    write_report(output_report, input_csv, rows, results)

    print(f"Rows used: {len(rows)}")
    print(f"Report written: {output_report}")


if __name__ == "__main__":
    main()
