from __future__ import annotations

import argparse
from pathlib import Path


def render_launcher_content(
    site: str,
    dat_path: str,
    year: int,
    day_of_year: int,
    workdir: str,
    dry_run: bool,
    python_exe: str,
) -> str:
    dry_run_value = "1" if dry_run else "0"
    return f"""@echo off
setlocal

set \"PYTHON_EXE={python_exe}\"
set \"WORKDIR=%~dp0..\\{workdir}\"
set \"DAT_PATH={dat_path}\"
set \"YEAR={year}\"
set \"DAY_OF_YEAR={day_of_year}\"
set \"SITE={site}\"
set \"ELEVATION_CUTOFF=10\"
set \"TIME_STEP_HOURS=0.5\"
set \"CORRECTION_COEFFICIENT=0.97\"
set \"DRY_RUN={dry_run_value}\"

set \"PYTHON_CMD=%PYTHON_EXE%\"
if not exist \"%PYTHON_EXE%\" (
    where py >nul 2>&1
    if errorlevel 1 (
        echo Python was not found. Update PYTHON_EXE in this file.
        pause
        exit /b 1
    )
    set \"PYTHON_CMD=py -3\"
)

set \"EXTRA_ARGS=\"
if \"%DRY_RUN%\"==\"1\" set \"EXTRA_ARGS=--dry-run\"

%PYTHON_CMD% \"%~dp0..\\run_absoltec.py\" ^
    --workdir \"%WORKDIR%\" ^
    --dat-path \"%DAT_PATH%\" ^
    --elevation-cutoff \"%ELEVATION_CUTOFF%\" ^
    --year \"%YEAR%\" ^
    --day-of-year \"%DAY_OF_YEAR%\" ^
    --site \"%SITE%\" ^
    --time-step-hours \"%TIME_STEP_HOURS%\" ^
    --correction-coefficient \"%CORRECTION_COEFFICIENT%\" ^
    %EXTRA_ARGS%

if errorlevel 1 (
    echo.
    echo Failed for site %SITE%.
    pause
    exit /b 1
)

echo.
echo Completed successfully for site %SITE%.
pause
"""


def discover_stations(stations_root: Path) -> list[str]:
    if not stations_root.exists():
        raise FileNotFoundError(f"Stations root not found: {stations_root}")
    stations = [p.name for p in stations_root.iterdir() if p.is_dir()]
    return sorted(stations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one .bat launcher per station for run_absoltec.py"
    )
    parser.add_argument("--dat-path", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--day-of-year", type=int, required=True)
    parser.add_argument(
        "--stations",
        help="Comma-separated station list. If omitted, --stations-root is used.",
    )
    parser.add_argument(
        "--stations-root",
        help="Directory containing station subfolders (used when --stations is omitted).",
    )
    parser.add_argument("--output-dir", default="launchers")
    parser.add_argument("--workdir", default="TayAbsTEC_24.04.17")
    parser.add_argument("--python-exe", default=r"C:\Python\Python312\python.exe")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (1 <= args.day_of_year <= 366):
        raise ValueError("--day-of-year must be in range 1..366")

    if args.stations:
        stations = [value.strip() for value in args.stations.split(",") if value.strip()]
    else:
        stations_root = (
            Path(args.stations_root)
            if args.stations_root
            else Path("in") / str(args.year) / f"{args.day_of_year:03d}"
        )
        stations = discover_stations(stations_root)

    if not stations:
        raise ValueError("No stations found. Provide --stations or valid --stations-root.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for site in stations:
        content = render_launcher_content(
            site=site,
            dat_path=args.dat_path,
            year=args.year,
            day_of_year=args.day_of_year,
            workdir=args.workdir,
            dry_run=args.dry_run,
            python_exe=args.python_exe,
        )
        launcher_name = f"run_{site}_{args.year}_{args.day_of_year:03d}.bat"
        launcher_path = output_dir / launcher_name
        launcher_path.write_text(content, encoding="utf-8", newline="\r\n")

    print(f"Generated {len(stations)} launcher(s) in: {output_dir.resolve()}")


if __name__ == "__main__":
    main()