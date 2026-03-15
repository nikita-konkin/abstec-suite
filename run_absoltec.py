from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
from pathlib import Path


def normalize_dat_path(dat_path: str) -> str:
    normalized = dat_path.strip()
    if not normalized.endswith(("\\", "/")):
        normalized = normalized + "\\"
    return normalized


def should_use_wine(exe_path: Path, runner: str) -> bool:
    if runner == "wine":
        return True
    if runner == "direct":
        return False
    if platform.system() == "Windows":
        return False
    return exe_path.suffix.lower() == ".exe"


def is_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value.strip()))


def to_wine_windows_path(path_value: str) -> str:
    if is_windows_path(path_value):
        return path_value

    path_obj = Path(path_value).expanduser().resolve()
    posix_path = path_obj.as_posix().lstrip("/")
    return f"Z:\\{posix_path.replace('/', '\\\\')}"


def build_dia_content(
    dat_path: str,
    elevation_cutoff: float,
    year: int,
    day_of_year: int,
    site: str,
    time_step_hours: float,
    correction_coefficient: float,
) -> str:
    normalized_dat_path = normalize_dat_path(dat_path)
    lines = [
        normalized_dat_path,
        str(elevation_cutoff),
        str(year),
        str(day_of_year),
        site,
        str(time_step_hours),
        str(correction_coefficient),
    ]
    return "\n".join(lines) + "\n"


def update_dia_file(
    dia_path: Path,
    dat_path: str,
    elevation_cutoff: float,
    year: int,
    day_of_year: int,
    site: str,
    time_step_hours: float,
    correction_coefficient: float,
) -> None:
    dia_content = build_dia_content(
        dat_path=dat_path,
        elevation_cutoff=elevation_cutoff,
        year=year,
        day_of_year=day_of_year,
        site=site,
        time_step_hours=time_step_hours,
        correction_coefficient=correction_coefficient,
    )
    dia_path.write_text(dia_content, encoding="utf-8")


def find_matching_dat_files(dat_path: Path, site: str, day_of_year: int, year: int) -> tuple[list[Path], str]:
    # pattern = f"{site}_*_{day_of_year:03d}_{year % 100:02d}.dat"
    pattern = f"{''.join(site[:4])}_*_{day_of_year:03d}_{year % 100:02d}.dat"
    return sorted(dat_path.glob(pattern)), pattern


def resolve_station_data_folder(dat_root: Path, site: str, day_of_year: int, year: int) -> Path:
    day_root = dat_root / str(year) / f"{day_of_year:03d}"
    exact_path = day_root / site
    if exact_path.exists():
        return exact_path

    if not day_root.exists():
        return exact_path

    site_lower = site.lower()
    starts_with_site = [
        path for path in day_root.iterdir() if path.is_dir() and path.name.lower().startswith(site_lower)
    ]
    if len(starts_with_site) == 1:
        return starts_with_site[0]

    site_prefix = site_lower[:4]
    starts_with_prefix = [
        path for path in day_root.iterdir() if path.is_dir() and path.name.lower().startswith(site_prefix)
    ]
    if len(starts_with_prefix) == 1:
        return starts_with_prefix[0]

    return exact_path


def validate_dat_inputs(dat_path: Path, site: str, day_of_year: int, year: int) -> Path:
    station_folder = resolve_station_data_folder(dat_path, site, day_of_year, year)
    if not station_folder.exists():
        raise FileNotFoundError(
            f"Station input folder not found: {station_folder}. "
            "Expected layout: IN_ROOT/YYYY/DDD/SITE. "
            f"Resolved from site='{site}'."
        )

    matching_files, pattern = find_matching_dat_files(station_folder, site, day_of_year, year)
    if not matching_files:
        example_files = sorted(station_folder.glob("*.dat"))[:5]
        examples = ", ".join(path.name for path in example_files) if example_files else "none"
        raise FileNotFoundError(
            f"No input files matched pattern '{pattern}' in {station_folder}.\n"
            f"Examples found: {examples}\n"
            f"Was found station_folder: {station_folder}\n"
            f"{''.join(site[:4])}_{day_of_year:03d}_{year % 100:02d}.dat"
        )

    nonzero_bias_values = 0
    checked_rows = 0
    for dat_file in matching_files:
        for raw_line in dat_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped_line = raw_line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue

            parts = stripped_line.split()
            if len(parts) < 7:
                continue

            checked_rows += 1
            try:
                if abs(float(parts[5])) > 1e-9:
                    nonzero_bias_values += 1
                    break
            except ValueError:
                continue

        if nonzero_bias_values > 0:
            break

    if checked_rows == 0:
        raise ValueError(
            "Matched .dat files contain no usable numeric rows. "
            "Check tec-suite export format (tsn, hour, el, az, tec.l1l2, tec.p1p2 or tec.c1p2, validity)."
        )

    if nonzero_bias_values == 0:
        raise ValueError(
            "All matched .dat files have 0.000 in TEC bias column (6th data column). "
            "TayAbsTEC cannot proceed. Re-export tec-suite data with a valid tec.p1p2/tec.c1p2 column."
        )

    return station_folder


def find_wine_binary() -> str | None:
    wine_path = shutil.which("wine") or shutil.which("wine64")
    if wine_path:
        return wine_path

    known_paths = [
        Path("/usr/lib/wine/wine"),
        Path("/usr/lib/wine/wine64"),
        Path("/opt/homebrew/bin/wine64"),
        Path("/opt/homebrew/bin/wine"),
        Path("/usr/local/bin/wine"),
        Path("/usr/local/bin/wine64"),
    ]
    for candidate in known_paths:
        if candidate.exists():
            return str(candidate)

    return None


def resolve_runner_command(exe_path: Path, runner: str) -> list[str]:
    if runner == "direct":
        return [str(exe_path)]

    if runner == "wine":
        wine_path = find_wine_binary()
        if not wine_path:
            raise RuntimeError(
                "Wine is required to run Windows binaries on this OS but was not found. "
                "Install wine/wine64 or use --runner direct with a native executable."
            )
        validate_wine_runtime(wine_path)
        return [wine_path, str(exe_path)]

    if runner != "auto":
        raise ValueError("--runner must be one of: auto, direct, wine")

    if platform.system() == "Windows":
        return [str(exe_path)]

    if exe_path.suffix.lower() == ".exe":
        wine_path = find_wine_binary()
        if not wine_path:
            raise RuntimeError(
                "Detected Windows executable on non-Windows OS. "
                "Install wine/wine64 or pass --runner direct if this is a native binary."
            )
        validate_wine_runtime(wine_path)
        return [wine_path, str(exe_path)]

    return [str(exe_path)]


def validate_wine_runtime(wine_path: str) -> None:
    check = subprocess.run(
        [wine_path, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    if check.returncode == 0:
        return

    if check.returncode < 0:
        signal_number = -check.returncode
        if signal_number == 9:
            raise RuntimeError(
                "Wine runtime check failed: wine was terminated by SIGKILL while running '--version'. "
                "On macOS this is often caused by Gatekeeper quarantine or incompatible Wine install. "
                "Try: xattr -dr com.apple.quarantine /Applications/Wine\\ Stable.app, "
                "then re-run; also verify Rosetta on Apple Silicon."
            )
        raise RuntimeError(
            f"Wine runtime check failed: wine terminated by signal {signal_number}."
        )

    stderr_text = (check.stderr or "").strip()
    stdout_text = (check.stdout or "").strip()
    details = stderr_text or stdout_text or "unknown wine startup failure"
    raise RuntimeError(f"Wine runtime check failed: {details}")


def run_absoltec(exe_path: Path, runner: str) -> None:
    command = resolve_runner_command(exe_path, runner)

    result = subprocess.run(
        command,
        cwd=str(exe_path.parent),
        check=False,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    if result.returncode != 0:
        base_error = (
            f"absolTEC process failed (return code {result.returncode}). "
            f"Command: {' '.join(command)}"
        )
        if result.returncode < 0:
            signal_number = -result.returncode
            base_error = (
                f"absolTEC process was terminated by signal {signal_number}. "
                f"Command: {' '.join(command)}"
            )
            if signal_number == 9 and "wine" in Path(command[0]).name.lower():
                base_error += (
                    " Wine was killed by SIGKILL. Try running Wine manually once to initialize, "
                    "ensure Rosetta is installed on Apple Silicon, and check macOS security prompts/logs."
                )
        raise RuntimeError(base_error)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if "error, no files" in combined_output:
        raise RuntimeError("absolTEC reported 'Error, no files'. Check DAT_PATH and input files.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Edit absolTEC.dia and run absolTEC.exe"
    )
    parser.add_argument(
        "--workdir",
        default="TayAbsTEC_24.04.17",
        help="Directory containing absolTEC.dia and absolTEC.exe",
    )
    parser.add_argument(
        "--dat-path",
        required=True,
        help="Path to tec-suite .dat files (for line 1 in absolTEC.dia)",
    )
    parser.add_argument("--elevation-cutoff", type=float, default=10.0)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--day-of-year", type=int, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--time-step-hours", type=float, default=0.5)
    parser.add_argument("--correction-coefficient", type=float, default=0.97)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only edit absolTEC.dia without running absolTEC.exe",
    )
    parser.add_argument(
        "--runner",
        choices=["auto", "direct", "wine"],
        default="auto",
        help="How to launch absolTEC executable (default: auto)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (1 <= args.day_of_year <= 366):
        raise ValueError("--day-of-year must be in range 1..366")

    workdir = Path(args.workdir).resolve()
    dia_path = workdir / "absolTEC.dia"
    exe_path = workdir / "absolTEC.exe"

    if not workdir.exists():
        raise FileNotFoundError(f"Work directory not found: {workdir}")
    if not dia_path.exists():
        raise FileNotFoundError(f"absolTEC.dia not found: {dia_path}")
    if not args.dry_run and not exe_path.exists():
        raise FileNotFoundError(f"absolTEC.exe not found: {exe_path}")

    dat_path_obj = Path(args.dat_path)
    if not dat_path_obj.exists():
        raise FileNotFoundError(f"Data path not found: {dat_path_obj}")

    resolved_station_folder = validate_dat_inputs(dat_path_obj, args.site, args.day_of_year, args.year)

    dat_path_for_dia = args.dat_path
    if should_use_wine(exe_path, args.runner):
        dat_path_for_dia = to_wine_windows_path(args.dat_path)

    update_dia_file(
        dia_path=dia_path,
        dat_path=dat_path_for_dia,
        elevation_cutoff=args.elevation_cutoff,
        year=args.year,
        day_of_year=args.day_of_year,
        site=resolved_station_folder.name,
        time_step_hours=args.time_step_hours,
        correction_coefficient=args.correction_coefficient,
    )
    print(f"Updated: {dia_path}")

    if args.dry_run:
        print("Dry run enabled. Skipping absolTEC.exe execution.")
        return

    run_absoltec(exe_path, args.runner)
    print(f"Finished: {exe_path}")


if __name__ == "__main__":
    main()