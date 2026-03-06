from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


FEATURE_NAMES = ["1", "e", "t", "c", "e2", "t2", "c2", "e_t", "e_c", "t_c"]


@dataclass
class ScenarioRow:
    scenario: str
    elevation: float
    timestep: float
    coefficient: float
    snapshot_output_file: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract UT-by-UT Iv models from experiment snapshots"
    )
    parser.add_argument(
        "--input-csv",
        default="experiments/absoltec_experiments_aksu_2026_001_snap.csv",
    )
    parser.add_argument(
        "--output-report",
        default="experiments/absoltec_time_model_report_aksu_2026_001.md",
    )
    parser.add_argument(
        "--min-samples-per-ut",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--top-ut-count",
        type=int,
        default=20,
        help="How many UT models (best R2) to print in detail",
    )
    return parser.parse_args()


def build_features(elevation: float, timestep: float, coefficient: float) -> list[float]:
    e = elevation
    t = timestep
    c = coefficient
    return [1.0, e, t, c, e * e, t * t, c * c, e * t, e * c, t * c]


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


def solve_linear_system(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(a)
    augmented = [row[:] + [b[i]] for i, row in enumerate(a)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < 1e-12:
            raise ValueError("Singular matrix")

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


def fit_model(x_rows: list[list[float]], y_values: list[float], ridge: float = 1e-8) -> list[float]:
    xt = transpose(x_rows)
    xtx = matmul(xt, x_rows)
    xty = [sum(xt[i][k] * y_values[k] for k in range(len(y_values))) for i in range(len(xt))]
    for i in range(len(xtx)):
        xtx[i][i] += ridge
    return solve_linear_system(xtx, xty)


def model_metrics(y_true: list[float], y_pred: list[float]) -> tuple[float, float, float]:
    n = len(y_true)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean_y = sum(y_true) / n
    ss_tot = sum((v - mean_y) ** 2 for v in y_true)
    ss_res = sum((y_true[i] - y_pred[i]) ** 2 for i in range(n))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
    mae = sum(abs(y_true[i] - y_pred[i]) for i in range(n)) / n
    rmse = (ss_res / n) ** 0.5
    return r2, mae, rmse


def parse_snapshot_output(output_file: Path) -> dict[float, float]:
    ut_to_iv: dict[float, float] = {}
    if not output_file.exists():
        return ut_to_iv
    for line in output_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        try:
            ut = float(parts[0])
            iv = float(parts[1])
        except ValueError:
            continue
        ut_to_iv[ut] = iv
    return ut_to_iv


def format_equation(coefficients: list[float]) -> str:
    chunks: list[str] = []
    for i, coef in enumerate(coefficients):
        term = FEATURE_NAMES[i]
        if i == 0:
            chunks.append(f"{coef:.6f}")
        else:
            sign = "+" if coef >= 0 else "-"
            chunks.append(f" {sign} {abs(coef):.6f}*{term}")
    return "".join(chunks)


def load_scenarios(input_csv: Path) -> list[ScenarioRow]:
    scenarios: list[ScenarioRow] = []
    with input_csv.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("status") != "ok":
                continue
            snapshot_path = row.get("snapshot_output_file", "").strip()
            if not snapshot_path:
                continue
            scenarios.append(
                ScenarioRow(
                    scenario=row.get("scenario", ""),
                    elevation=float(row["elevation_cutoff"]),
                    timestep=float(row["time_step_hours"]),
                    coefficient=float(row["correction_coefficient"]),
                    snapshot_output_file=Path(snapshot_path),
                )
            )
    if not scenarios:
        raise ValueError("No usable scenarios with snapshot_output_file found")
    return scenarios


def main() -> None:
    args = parse_args()
    input_csv = Path(args.input_csv).resolve()
    output_report = Path(args.output_report).resolve()

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    scenarios = load_scenarios(input_csv)

    ut_samples: dict[float, list[tuple[list[float], float]]] = {}
    for scenario in scenarios:
        ut_iv = parse_snapshot_output(scenario.snapshot_output_file)
        features = build_features(scenario.elevation, scenario.timestep, scenario.coefficient)
        for ut, iv in ut_iv.items():
            ut_samples.setdefault(ut, []).append((features, iv))

    model_rows: list[dict[str, object]] = []
    for ut, samples in sorted(ut_samples.items(), key=lambda item: item[0]):
        if len(samples) < args.min_samples_per_ut:
            continue
        x_rows = [item[0] for item in samples]
        y_values = [item[1] for item in samples]

        coefficients = fit_model(x_rows, y_values)
        predictions = [sum(coefficients[i] * x[i] for i in range(len(coefficients))) for x in x_rows]
        r2, mae, rmse = model_metrics(y_values, predictions)

        model_rows.append(
            {
                "ut": ut,
                "samples": len(samples),
                "r2": r2,
                "mae": mae,
                "rmse": rmse,
                "equation": format_equation(coefficients),
            }
        )

    if not model_rows:
        raise ValueError("No UT models were generated; try lowering --min-samples-per-ut")

    best_rows = sorted(model_rows, key=lambda row: row["r2"], reverse=True)[: args.top_ut_count]
    avg_r2 = sum(row["r2"] for row in model_rows) / len(model_rows)

    output_report.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# absolTEC UT-by-UT model report")
    lines.append("")
    lines.append(f"Input CSV: {input_csv}")
    lines.append(f"Scenarios with snapshots: {len(scenarios)}")
    lines.append(f"UT models generated: {len(model_rows)}")
    lines.append(f"Average R2 across UT models: {avg_r2:.6f}")
    lines.append("")
    lines.append("Feature basis: [1, e, t, c, e2, t2, c2, e_t, e_c, t_c]")
    lines.append("")
    lines.append("## Top UT models by R2")
    lines.append("")
    for row in best_rows:
        lines.append(
            f"- UT={row['ut']:.3f}, samples={row['samples']}, R2={row['r2']:.6f}, "
            f"MAE={row['mae']:.6f}, RMSE={row['rmse']:.6f}"
        )
        lines.append(f"  - Iv(UT={row['ut']:.3f}) = {row['equation']}")
    lines.append("")

    output_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"UT models: {len(model_rows)}")
    print(f"Report written: {output_report}")


if __name__ == "__main__":
    main()
