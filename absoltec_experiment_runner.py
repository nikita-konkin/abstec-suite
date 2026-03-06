from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Iterable

from run_absoltec import run_absoltec, update_dia_file, validate_dat_inputs


@dataclass
class Scenario:
    name: str
    elevation_cutoff: float
    time_step_hours: float
    correction_coefficient: float


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def build_scenarios(
    base_elevation: float,
    base_time_step: float,
    base_coefficient: float,
    elevation_values: Iterable[float],
    time_step_values: Iterable[float],
    coefficient_values: Iterable[float],
    mode: str,
) -> list[Scenario]:
    if mode == "full-grid":
        scenarios: list[Scenario] = []
        for elevation, timestep, coefficient in product(
            elevation_values,
            time_step_values,
            coefficient_values,
        ):
            scenarios.append(
                Scenario(
                    name=f"grid_e{elevation:g}_t{timestep:g}_c{coefficient:g}",
                    elevation_cutoff=elevation,
                    time_step_hours=timestep,
                    correction_coefficient=coefficient,
                )
            )
        return scenarios

    scenarios: list[Scenario] = [
        Scenario(
            name="baseline",
            elevation_cutoff=base_elevation,
            time_step_hours=base_time_step,
            correction_coefficient=base_coefficient,
        )
    ]

    for value in elevation_values:
        if value == base_elevation:
            continue
        scenarios.append(
            Scenario(
                name=f"elevation_{value:g}",
                elevation_cutoff=value,
                time_step_hours=base_time_step,
                correction_coefficient=base_coefficient,
            )
        )

    for value in time_step_values:
        if value == base_time_step:
            continue
        scenarios.append(
            Scenario(
                name=f"timestep_{value:g}",
                elevation_cutoff=base_elevation,
                time_step_hours=value,
                correction_coefficient=base_coefficient,
            )
        )

    for value in coefficient_values:
        if value == base_coefficient:
            continue
        scenarios.append(
            Scenario(
                name=f"coef_{value:g}",
                elevation_cutoff=base_elevation,
                time_step_hours=base_time_step,
                correction_coefficient=value,
            )
        )

    return scenarios


def parse_iv_stats(output_file: Path) -> tuple[int, float]:
    if not output_file.exists():
        return 0, 0.0

    non_zero_count = 0
    max_iv = 0.0
    for line in output_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue

        try:
            iv_value = float(parts[1])
        except ValueError:
            continue

        if abs(iv_value) > 1e-9:
            non_zero_count += 1
        if iv_value > max_iv:
            max_iv = iv_value

    return non_zero_count, max_iv


def parse_dcb_count(dcb_file: Path) -> int:
    if not dcb_file.exists():
        return 0
    count = 0
    for line in dcb_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run absolTEC parameter experiments and write CSV report"
    )
    parser.add_argument("--workdir", default="TayAbsTEC_24.04.17")
    parser.add_argument("--in-root", default="in")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--day-of-year", type=int, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--base-elevation", type=float, default=10.0)
    parser.add_argument("--base-time-step", type=float, default=0.5)
    parser.add_argument("--base-coefficient", type=float, default=0.97)
    parser.add_argument("--elevation-values", default="5,10,15")
    parser.add_argument("--time-step-values", default="0.25,0.5,1.0")
    parser.add_argument("--coefficient-values", default="0.87,0.94,0.97")
    parser.add_argument(
        "--mode",
        choices=["one-factor", "full-grid"],
        default="one-factor",
        help="one-factor: vary one parameter at a time; full-grid: run all combinations",
    )
    parser.add_argument("--report-path", help="Path to output CSV report")
    parser.add_argument(
        "--snapshot-dir",
        help="Optional directory to store per-scenario output snapshots",
    )
    parser.add_argument(
        "--keep-dia",
        action="store_true",
        help="Keep modified absolTEC.dia from the last scenario",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (1 <= args.day_of_year <= 366):
        raise ValueError("--day-of-year must be in range 1..366")

    workdir = Path(args.workdir).resolve()
    in_root = Path(args.in_root).resolve()
    dia_path = workdir / "absolTEC.dia"
    exe_path = workdir / "absolTEC.exe"

    if not workdir.exists():
        raise FileNotFoundError(f"Work directory not found: {workdir}")
    if not dia_path.exists():
        raise FileNotFoundError(f"absolTEC.dia not found: {dia_path}")
    if not exe_path.exists():
        raise FileNotFoundError(f"absolTEC.exe not found: {exe_path}")
    if not in_root.exists():
        raise FileNotFoundError(f"Input root not found: {in_root}")

    validate_dat_inputs(in_root, args.site, args.day_of_year, args.year)

    scenarios = build_scenarios(
        base_elevation=args.base_elevation,
        base_time_step=args.base_time_step,
        base_coefficient=args.base_coefficient,
        elevation_values=parse_float_list(args.elevation_values),
        time_step_values=parse_float_list(args.time_step_values),
        coefficient_values=parse_float_list(args.coefficient_values),
        mode=args.mode,
    )

    report_path = (
        Path(args.report_path).resolve()
        if args.report_path
        else Path("experiments")
        / f"absoltec_experiments_{args.site}_{args.year}_{args.day_of_year:03d}.csv"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot_dir = Path(args.snapshot_dir).resolve() if args.snapshot_dir else None
    if snapshot_dir:
        snapshot_dir.mkdir(parents=True, exist_ok=True)

    output_file = workdir / str(args.year) / args.site / f"{args.site}_{args.day_of_year:03d}_{args.year}.dat"
    dcb_file = workdir / str(args.year) / args.site / f"DCB_{args.site}_{args.day_of_year:03d}_{args.year}.dat"

    original_dia = dia_path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []

    try:
        for scenario in scenarios:
            status = "ok"
            error = ""

            update_dia_file(
                dia_path=dia_path,
                dat_path=str(in_root),
                elevation_cutoff=scenario.elevation_cutoff,
                year=args.year,
                day_of_year=args.day_of_year,
                site=args.site,
                time_step_hours=scenario.time_step_hours,
                correction_coefficient=scenario.correction_coefficient,
            )

            try:
                run_absoltec(exe_path)
            except Exception as exc:
                status = "error"
                error = str(exc).replace("\n", " ")

            snapshot_output_file = ""
            snapshot_dcb_file = ""
            if snapshot_dir:
                scenario_output_file = snapshot_dir / f"{args.site}_{args.year}_{args.day_of_year:03d}_{scenario.name}.dat"
                scenario_dcb_file = snapshot_dir / f"DCB_{args.site}_{args.year}_{args.day_of_year:03d}_{scenario.name}.dat"
                if output_file.exists():
                    shutil.copy2(output_file, scenario_output_file)
                    snapshot_output_file = str(scenario_output_file)
                if dcb_file.exists():
                    shutil.copy2(dcb_file, scenario_dcb_file)
                    snapshot_dcb_file = str(scenario_dcb_file)

            iv_non_zero_count, iv_max = parse_iv_stats(output_file)
            dcb_count = parse_dcb_count(dcb_file)

            rows.append(
                {
                    "scenario": scenario.name,
                    "status": status,
                    "error": error,
                    "elevation_cutoff": f"{scenario.elevation_cutoff}",
                    "time_step_hours": f"{scenario.time_step_hours}",
                    "correction_coefficient": f"{scenario.correction_coefficient}",
                    "iv_non_zero_count": f"{iv_non_zero_count}",
                    "iv_max": f"{iv_max:.6f}",
                    "dcb_entry_count": f"{dcb_count}",
                    "output_file": str(output_file),
                    "dcb_file": str(dcb_file),
                    "snapshot_output_file": snapshot_output_file,
                    "snapshot_dcb_file": snapshot_dcb_file,
                }
            )

    finally:
        if not args.keep_dia:
            dia_path.write_text(original_dia, encoding="utf-8")

    with report_path.open("w", newline="", encoding="utf-8") as csv_file:
        fieldnames = [
            "scenario",
            "status",
            "error",
            "elevation_cutoff",
            "time_step_hours",
            "correction_coefficient",
            "iv_non_zero_count",
            "iv_max",
            "dcb_entry_count",
            "output_file",
            "dcb_file",
            "snapshot_output_file",
            "snapshot_dcb_file",
        ]
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Scenarios run: {len(rows)}")
    print(f"Report written: {report_path}")
    if not args.keep_dia:
        print(f"Restored: {dia_path}")


if __name__ == "__main__":
    main()
