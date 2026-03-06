from __future__ import annotations

import argparse
import csv
from pathlib import Path

from run_absoltec import resolve_station_data_folder


OUTPUT_COLUMNS = [
    "I_v",
    "G_lon",
    "G_lat",
    "G_q_lon",
    "G_q_lat",
    "G_t",
    "G_q_t",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train a simple model from IN data and predict TayAbsTEC-like output"
    )
    parser.add_argument("--input-root", default="in")
    parser.add_argument("--output-root", default="TayAbsTEC_24.04.17")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--day-of-year", type=int, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--time-step", type=float, default=0.5)
    parser.add_argument(
        "--predict-hours",
        type=float,
        default=24.0,
        help="Prediction horizon in hours (default full day)",
    )
    parser.add_argument(
        "--max-ut-distance",
        type=float,
        default=0.8,
        help="Maximum allowed |UT_output - UT_input_bin| for training/prediction alignment",
    )
    parser.add_argument(
        "--predicted-file",
        help="Optional output file path for predicted TayAbsTEC-like data",
    )
    parser.add_argument(
        "--reference-output-file",
        help="Optional specific TayAbsTEC output file to use as training target",
    )
    parser.add_argument(
        "--metrics-file",
        default="experiments/absoltec_input_output_metrics.csv",
        help="Where to write fit metrics",
    )
    return parser.parse_args()


def ut_bin(hour: float, step: float) -> float:
    return round(round(hour / step) * step, 3)


def find_input_files(input_folder: Path, site: str, day_of_year: int, year: int) -> list[Path]:
    prefix = site[:4]
    pattern = f"{prefix}_*_{day_of_year:03d}_{year % 100:02d}.dat"
    return sorted(input_folder.glob(pattern))


def parse_input_features(input_files: list[Path], step: float) -> dict[float, list[float]]:
    grouped: dict[float, dict[str, float]] = {}

    for input_file in input_files:
        for line in input_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            parts = stripped.split()
            if len(parts) < 7:
                continue

            try:
                hour = float(parts[1])
                el = float(parts[2])
                tec_l1l2 = float(parts[4])
                tec_p1p2 = float(parts[5])
                validity = float(parts[6])
            except ValueError:
                continue

            bucket = ut_bin(hour, step)
            if bucket not in grouped:
                grouped[bucket] = {
                    "count": 0.0,
                    "sum_el": 0.0,
                    "sum_l1l2": 0.0,
                    "sum_p1p2": 0.0,
                    "sum_l1l2_sq": 0.0,
                    "sum_p1p2_sq": 0.0,
                    "valid_count": 0.0,
                }

            row = grouped[bucket]
            row["count"] += 1.0
            row["sum_el"] += el
            row["sum_l1l2"] += tec_l1l2
            row["sum_p1p2"] += tec_p1p2
            row["sum_l1l2_sq"] += tec_l1l2 * tec_l1l2
            row["sum_p1p2_sq"] += tec_p1p2 * tec_p1p2
            if validity > 0:
                row["valid_count"] += 1.0

    features: dict[float, list[float]] = {}
    for bucket, row in grouped.items():
        count = max(row["count"], 1.0)
        mean_el = row["sum_el"] / count
        mean_l1l2 = row["sum_l1l2"] / count
        mean_p1p2 = row["sum_p1p2"] / count
        var_l1l2 = max(row["sum_l1l2_sq"] / count - mean_l1l2 * mean_l1l2, 0.0)
        var_p1p2 = max(row["sum_p1p2_sq"] / count - mean_p1p2 * mean_p1p2, 0.0)
        std_l1l2 = var_l1l2 ** 0.5
        std_p1p2 = var_p1p2 ** 0.5
        frac_valid = row["valid_count"] / count

        features[bucket] = [
            1.0,
            count,
            mean_el,
            mean_l1l2,
            mean_p1p2,
            std_l1l2,
            std_p1p2,
            frac_valid,
            mean_l1l2 * mean_l1l2,
            mean_p1p2 * mean_p1p2,
            mean_l1l2 * mean_p1p2,
        ]

    return features


def parse_target_output(output_file: Path) -> dict[float, list[float]]:
    targets: dict[float, list[float]] = {}
    for line in output_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 8:
            continue
        try:
            ut = float(parts[0])
            values = [float(parts[i]) for i in range(1, 8)]
        except ValueError:
            continue
        targets[ut] = values
    return targets


def snap_ut_map(values_by_ut: dict[float, list[float]], step: float) -> dict[float, list[float]]:
    snapped: dict[float, list[float]] = {}
    for ut, values in values_by_ut.items():
        snapped[ut_bin(ut, step)] = values
    return snapped


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


def fit_model(x_rows: list[list[float]], y_values: list[float], ridge: float = 1e-6) -> list[float]:
    xt = transpose(x_rows)
    xtx = matmul(xt, x_rows)
    xty = [sum(xt[i][k] * y_values[k] for k in range(len(y_values))) for i in range(len(xt))]
    for i in range(len(xtx)):
        xtx[i][i] += ridge
    return solve_linear_system(xtx, xty)


def predict(coefficients: list[float], x_row: list[float]) -> float:
    return sum(coefficients[i] * x_row[i] for i in range(len(coefficients)))


def r2_score(y_true: list[float], y_pred: list[float]) -> float:
    if not y_true:
        return 0.0
    mean_y = sum(y_true) / len(y_true)
    ss_tot = sum((v - mean_y) ** 2 for v in y_true)
    ss_res = sum((y_true[i] - y_pred[i]) ** 2 for i in range(len(y_true)))
    if ss_tot < 1e-12:
        return 1.0
    return 1.0 - ss_res / ss_tot


def nearest_feature_row(
    features_by_ut: dict[float, list[float]],
    target_ut: float,
    max_distance: float,
) -> list[float] | None:
    if not features_by_ut:
        return None
    best_ut = min(features_by_ut.keys(), key=lambda ut: abs(ut - target_ut))
    if abs(best_ut - target_ut) > max_distance:
        return None
    return features_by_ut[best_ut]


def main() -> None:
    args = parse_args()

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()

    input_folder = resolve_station_data_folder(input_root, args.site, args.day_of_year, args.year)
    if not input_folder.exists():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    input_files = find_input_files(input_folder, args.site, args.day_of_year, args.year)
    if not input_files:
        raise FileNotFoundError(f"No input files found in {input_folder}")

    output_file = (
        Path(args.reference_output_file).resolve()
        if args.reference_output_file
        else output_root / str(args.year) / args.site / f"{args.site}_{args.day_of_year:03d}_{args.year}.dat"
    )
    if not output_file.exists():
        raise FileNotFoundError(f"Reference output file not found: {output_file}")

    features_by_ut = parse_input_features(input_files, args.time_step)
    target_by_ut = snap_ut_map(parse_target_output(output_file), args.time_step)

    target_ut_sorted = sorted(target_by_ut.keys())
    matched_ut: list[float] = []
    x_rows: list[list[float]] = []
    for ut in target_ut_sorted:
        feature_row = nearest_feature_row(features_by_ut, ut, args.max_ut_distance)
        if feature_row is None:
            continue
        matched_ut.append(ut)
        x_rows.append(feature_row)

    if len(matched_ut) < 5:
        raise ValueError(
            f"Not enough matched UT rows to train model: {len(matched_ut)}. "
            "Try increasing --max-ut-distance or use data with wider UT coverage."
        )

    target_vectors: dict[str, list[float]] = {}
    for column_index, column_name in enumerate(OUTPUT_COLUMNS):
        target_vectors[column_name] = [target_by_ut[ut][column_index] for ut in matched_ut]

    coefficients_by_target: dict[str, list[float]] = {}
    metrics_rows: list[dict[str, str]] = []

    for column_name in OUTPUT_COLUMNS:
        y_true = target_vectors[column_name]
        coefficients = fit_model(x_rows, y_true)
        y_pred = [predict(coefficients, x) for x in x_rows]
        coefficients_by_target[column_name] = coefficients
        metrics_rows.append(
            {
                "site": args.site,
                "year": str(args.year),
                "day_of_year": f"{args.day_of_year:03d}",
                "target": column_name,
                "r2": f"{r2_score(y_true, y_pred):.6f}",
                "samples": str(len(matched_ut)),
            }
        )

    predicted_file = (
        Path(args.predicted_file).resolve()
        if args.predicted_file
        else Path("experiments") / f"predicted_{args.site}_{args.day_of_year:03d}_{args.year}.dat"
    )
    predicted_file.parent.mkdir(parents=True, exist_ok=True)

    with predicted_file.open("w", encoding="utf-8", newline="") as out:
        out.write("# UT  I_v  G_lon  G_lat  G_q_lon  G_q_lat  G_t  G_q_t\n")
        predict_steps = int(args.predict_hours / args.time_step)
        predict_ut_sorted = [round(i * args.time_step, 3) for i in range(predict_steps)]
        for ut in predict_ut_sorted:
            x_row = nearest_feature_row(features_by_ut, ut, args.max_ut_distance)
            if x_row is None:
                predicted_values = [0.0 for _ in OUTPUT_COLUMNS]
            else:
                predicted_values = [predict(coefficients_by_target[name], x_row) for name in OUTPUT_COLUMNS]
            out.write(
                f"{ut:7.3f}"
                + "".join(f" {value:10.3f}" for value in predicted_values)
                + "\n"
            )

    metrics_file = Path(args.metrics_file).resolve()
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not metrics_file.exists()
    with metrics_file.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["site", "year", "day_of_year", "target", "r2", "samples"])
        if write_header:
            writer.writeheader()
        writer.writerows(metrics_rows)

    print(f"Input folder: {input_folder}")
    print(f"Reference output: {output_file}")
    print(f"Matched UT rows for training: {len(matched_ut)}")
    print(f"Predicted file: {predicted_file}")
    print(f"Metrics file: {metrics_file}")


if __name__ == "__main__":
    main()
