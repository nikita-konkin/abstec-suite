from __future__ import annotations

import argparse
import hashlib
import logging
import os
import platform
import re
import signal
import shutil
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)


PROGRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Example: "INFO: Completed 2618 / 15221"
    re.compile(r"Completed\s+(\d+)\s*/\s*(\d+)", flags=re.IGNORECASE),
    # Example: "INFO: Progress: 17%"
    re.compile(r"Progress:\s*(\d{1,3})\s*%", flags=re.IGNORECASE),
)


def _workdir_item_signature(item: Path) -> str:
    stat_result = item.stat()
    if item.is_file():
        digest = hashlib.sha256(item.read_bytes()).hexdigest()
        return f"file:{stat_result.st_size}:{digest}"

    child_names = sorted(child.name for child in item.iterdir())
    return f"dir:{stat_result.st_mtime_ns}:{stat_result.st_ctime_ns}:{'|'.join(child_names)}"


def parse_days_list(days_value: str) -> list[int]:
    days: list[int] = []
    for token in days_value.split(","):
        part = token.strip()
        if not part:
            continue

        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if start > end:
                raise ValueError(f"Invalid day range '{part}': start must be <= end")
            for day in range(start, end + 1):
                if not (1 <= day <= 366):
                    raise ValueError(f"Day out of range in --days: {day}")
                days.append(day)
            continue

        day = int(part)
        if not (1 <= day <= 366):
            raise ValueError(f"Day out of range in --days: {day}")
        days.append(day)

    if not days:
        raise ValueError("--days did not contain any valid day values")

    seen: set[int] = set()
    unique_days: list[int] = []
    for day in days:
        if day in seen:
            continue
        seen.add(day)
        unique_days.append(day)
    return unique_days


def parse_sites_list(site_value: str) -> list[str]:
    sites: list[str] = []
    for token in site_value.split(","):
        site = token.strip()
        if not site:
            continue
        sites.append(site)

    if not sites:
        raise ValueError("--site did not contain any valid station values")

    seen: set[str] = set()
    unique_sites: list[str] = []
    for site in sites:
        site_key = site.lower()
        if site_key in seen:
            continue
        seen.add(site_key)
        unique_sites.append(site)
    return unique_sites


def _matching_dat_file_names(station_dir: Path, site_prefix: str, day_of_year: int, year: int) -> set[str]:
    pattern = f"{site_prefix}_*_{day_of_year:03d}_{year % 100:02d}.dat"
    return {path.name.lower() for path in station_dir.glob(pattern)}


def discover_stations_for_day(dat_root: Path, year: int, day_of_year: int) -> list[str]:
    day_root = dat_root / str(year) / f"{day_of_year:03d}"
    if not day_root.exists():
        return []

    station_names = sorted(path.name for path in day_root.iterdir() if path.is_dir())
    station_dirs = {path.name.lower(): path for path in day_root.iterdir() if path.is_dir()}

    filtered_names: list[str] = []
    for name in station_names:
        name_lower = name.lower()
        if len(name) == 4:
            short_dir = station_dirs[name_lower]
            short_files = _matching_dat_file_names(short_dir, name_lower, day_of_year, year)
            if short_files:
                for other_name, other_dir in station_dirs.items():
                    if other_name == name_lower or not other_name.startswith(name_lower):
                        continue
                    other_files = _matching_dat_file_names(other_dir, name_lower, day_of_year, year)
                    if other_files == short_files:
                        logger.info(
                            "Skipping duplicate prefix-only station folder '%s' because station '%s' has the same matched DAT files",
                            name,
                            other_dir.name,
                        )
                        break
                else:
                    filtered_names.append(name)
                    continue
                continue
        filtered_names.append(name)

    return filtered_names


def build_station_run_plan(dat_root: Path, year: int, days_to_process: list[int]) -> list[tuple[int, str]]:
    plan: list[tuple[int, str]] = []
    for day_of_year in days_to_process:
        stations = discover_stations_for_day(dat_root, year, day_of_year)
        if not stations:
            logger.warning("No station folders found for %s/%03d", year, day_of_year)
            continue
        for site in stations:
            plan.append((day_of_year, site))
    return plan


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


def validate_runtime_architecture(exe_path: Path, runner: str) -> None:
    if not should_use_wine(exe_path, runner):
        return

    if platform.system() != "Linux":
        return

    machine = platform.machine().lower()
    if machine not in {"arm64", "aarch64"}:
        return

    if exe_path.suffix.lower() != ".exe":
        return

    raise RuntimeError(
        "Detected Linux arm64 runtime for a Windows .exe. "
        "This TayAbsTEC binary is 32-bit x86 and cannot run reliably in a native arm64 Wine container. "
        "Rebuild/run the container as linux/amd64, for example with Docker Compose platform=linux/amd64 or "
        "'docker build --platform linux/amd64 -t abstec-suite:latest .', or run absolTEC.exe directly on Windows."
    )


def extract_progress_counters(output_text: str) -> tuple[int, int] | None:
    latest_counter: tuple[int, int] | None = None

    for line in output_text.splitlines():
        completed_match = PROGRESS_PATTERNS[0].search(line)
        if completed_match:
            done = int(completed_match.group(1))
            total = int(completed_match.group(2))
            if total > 0:
                latest_counter = (done, total)
            continue

        percent_match = PROGRESS_PATTERNS[1].search(line)
        if percent_match:
            percent = int(percent_match.group(1))
            percent = max(0, min(percent, 100))
            latest_counter = (percent, 100)

    return latest_counter


def _kill_wine_process_group(proc: subprocess.Popen) -> None:
    """Send SIGKILL to the entire process group so winedbg and other Wine children are terminated."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_absoltec(exe_path: Path, runner: str, timeout_seconds: float | None = None) -> None:
    validate_runtime_architecture(exe_path, runner)
    command = resolve_runner_command(exe_path, runner)
    logger.info("Starting absolTEC execution: %s", " ".join(command))
    logger.info("absolTEC working directory: %s", exe_path.parent)

    proc = subprocess.Popen(
        command,
        cwd=str(exe_path.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_wine_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = exc.stdout or "", exc.stderr or ""
        output_hint = ""
        combined = f"{stdout}\n{stderr}".strip()
        if combined:
            output_hint = f" Last output: {combined[-400:]}"
        raise RuntimeError(
            f"absolTEC did not finish within {timeout_seconds} seconds. "
            f"Command: {' '.join(command)}."
            f"{output_hint}"
        ) from exc

    if stdout:
        logger.info(stdout.rstrip("\n"))
    if stderr:
        logger.error(stderr.rstrip("\n"))

    combined_streams = "\n".join(part for part in (stdout, stderr) if part)
    progress_counter = extract_progress_counters(combined_streams)
    if progress_counter:
        logger.info("Progress counter: %s / %s", progress_counter[0], progress_counter[1])

    if proc.returncode != 0:
        base_error = (
            f"absolTEC process failed (return code {proc.returncode}). "
            f"Command: {' '.join(command)}"
        )
        combined_output = f"{stdout}\n{stderr}".lower()
        if (
            "shellexecuteex failed: not enough memory" in combined_output
            or "failed to start l\"z:" in combined_output
            or "c0000135" in combined_output
        ):
            base_error += (
                " Wine failed to start this executable in the current Linux container runtime. "
                "This is often a Wine compatibility issue for this specific binary (not actual host RAM exhaustion). "
                "Use --dry-run in container and run absolTEC.exe directly on Windows for production execution."
            )
        if proc.returncode < 0:
            signal_number = -proc.returncode
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

    combined_output = f"{stdout}\n{stderr}".lower()
    if "error, no files" in combined_output:
        raise RuntimeError("absolTEC reported 'Error, no files'. Check DAT_PATH and input files.")


def capture_workdir_state(workdir: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for item in workdir.iterdir():
        if item.name in {"absolTEC.exe", "absolTEC.dia"}:
            continue
        try:
            state[item.name] = _workdir_item_signature(item)
        except FileNotFoundError:
            continue
    return state


def _move_with_merge(src: Path, dst: Path) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _move_with_merge(child, dst / child.name)
        try:
            src.rmdir()
        except OSError:
            pass
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def rename_station_output(output_dir: Path, year: int, site: str) -> Path | None:
    site_prefix = site[:4]
    if site_prefix == site:
        return None
    year_dir = output_dir / str(year)
    src = year_dir / site_prefix
    dst = year_dir / site
    if src.exists() and not dst.exists():
        src.rename(dst)
        return dst
    return None


def organize_station_output_by_day(output_dir: Path, year: int, day_of_year: int, site: str) -> Path | None:
    year_dir = output_dir / str(year)
    site_dir = year_dir / site
    if not site_dir.exists():
        return None

    day_dir = year_dir / f"{day_of_year:03d}"
    destination = day_dir / site
    if destination == site_dir:
        return destination

    _move_with_merge(site_dir, destination)
    return destination


def move_absoltec_results(
    workdir: Path,
    output_dir: Path,
    year: int,
    before_state: dict[str, str],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    year_dir = workdir / str(year)
    candidates: list[Path] = []
    if year_dir.exists():
        candidates.append(year_dir)
    else:
        for item in workdir.iterdir():
            if item.name in {"absolTEC.exe", "absolTEC.dia"}:
                continue
            previous_signature = before_state.get(item.name)
            try:
                current_signature = _workdir_item_signature(item)
            except FileNotFoundError:
                continue
            if previous_signature is None or current_signature != previous_signature:
                candidates.append(item)

    moved: list[Path] = []
    for candidate in candidates:
        destination = output_dir / candidate.name
        _move_with_merge(candidate, destination)
        moved.append(destination)

    return moved


def run_single_station(
    *,
    workdir: Path,
    dia_path: Path,
    exe_path: Path,
    dat_path_obj: Path,
    output_dir: Path | None,
    year: int,
    day_of_year: int,
    site: str,
    elevation_cutoff: float,
    time_step_hours: float,
    correction_coefficient: float,
    dry_run: bool,
    runner: str,
    execution_timeout_seconds: float,
    organize_by_day: bool,
) -> None:
    validate_dat_inputs(dat_path_obj, site, day_of_year, year)

    dat_path_for_dia = str(dat_path_obj)
    if should_use_wine(exe_path, runner):
        dat_path_for_dia = to_wine_windows_path(str(dat_path_obj))

    update_dia_file(
        dia_path=dia_path,
        dat_path=dat_path_for_dia,
        elevation_cutoff=elevation_cutoff,
        year=year,
        day_of_year=day_of_year,
        site=site,
        time_step_hours=time_step_hours,
        correction_coefficient=correction_coefficient,
    )
    logger.info("Updated: %s", dia_path)

    if dry_run:
        logger.info("Dry run enabled. Skipping absolTEC.exe execution.")
        return

    timeout_seconds: float | None = execution_timeout_seconds
    if timeout_seconds is not None and timeout_seconds <= 0:
        timeout_seconds = None

    before_state = capture_workdir_state(workdir) if output_dir else {}
    run_absoltec(exe_path, runner, timeout_seconds=timeout_seconds)
    logger.info("Finished: %s", exe_path)

    if output_dir:
        moved_paths = move_absoltec_results(workdir, output_dir, year, before_state)
        if moved_paths:
            logger.info(
                "Moved result paths to output directory: %s",
                ", ".join(str(path) for path in moved_paths),
            )
        else:
            logger.info("No generated result files were detected to move.")
        renamed = rename_station_output(output_dir, year, site)
        if renamed:
            logger.info("Renamed station output folder to: %s", renamed)
        if organize_by_day:
            organized = organize_station_output_by_day(output_dir, year, day_of_year, site)
            if organized:
                logger.info("Organized station output under day folder: %s", organized)


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
    parser.add_argument("--day-of-year", type=int)
    parser.add_argument("--site")
    parser.add_argument(
        "--days",
        help="Comma-separated list/ranges of days (e.g. 001,002,010-015). When set, all stations for each day are processed.",
    )
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
    parser.add_argument(
        "--execution-timeout-seconds",
        type=float,
        default=float(os.environ.get("EXECUTION_TIMEOUT_SECONDS", "300")),
        help="Max wait time for absolTEC process before failing (set 0 to disable).",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional directory where generated absolTEC results are moved after execution.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    if args.days:
        if args.day_of_year is not None:
            raise ValueError("Use either --day-of-year or --days, not both")
        if args.site is not None:
            raise ValueError("--site cannot be used with --days (stations are auto-discovered)")
        days_to_process = parse_days_list(args.days)
    else:
        if args.day_of_year is None:
            raise ValueError("--day-of-year is required when --days is not provided")
        if args.site is None:
            raise ValueError("--site is required when --days is not provided")
        if not (1 <= args.day_of_year <= 366):
            raise ValueError("--day-of-year must be in range 1..366")
        days_to_process = [args.day_of_year]

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

    resolved_output_dir = Path(args.output_dir).resolve() if args.output_dir else None

    if args.days:
        station_run_plan = build_station_run_plan(dat_path_obj, args.year, days_to_process)
        total_runs = len(station_run_plan)
        skipped_runs = 0
        for run_index, (day_of_year, site) in enumerate(station_run_plan, start=1):
            logger.info(
                "Processing year=%s day=%03d site=%s (%s/%s)",
                args.year,
                day_of_year,
                site,
                run_index,
                total_runs,
            )
            try:
                run_single_station(
                    workdir=workdir,
                    dia_path=dia_path,
                    exe_path=exe_path,
                    dat_path_obj=dat_path_obj,
                    output_dir=resolved_output_dir,
                    year=args.year,
                    day_of_year=day_of_year,
                    site=site,
                    elevation_cutoff=args.elevation_cutoff,
                    time_step_hours=args.time_step_hours,
                    correction_coefficient=args.correction_coefficient,
                    dry_run=args.dry_run,
                    runner=args.runner,
                    execution_timeout_seconds=args.execution_timeout_seconds,
                    organize_by_day=True,
                )
            except FileNotFoundError as exc:
                skipped_runs += 1
                logger.warning(
                    "Skipping year=%s day=%03d site=%s due to missing input files: %s",
                    args.year,
                    day_of_year,
                    site,
                    exc,
                )
            except ValueError as exc:
                skipped_runs += 1
                logger.warning(
                    "Skipping year=%s day=%03d site=%s due to invalid input data: %s",
                    args.year,
                    day_of_year,
                    site,
                    exc,
                )

            percent = round(run_index * 100 / total_runs)
            logger.info("Completed %s / %s", run_index, total_runs)
            logger.info("Progress: %s%%", percent)

        logger.info(
            "Batch run complete. Total station runs: %s, skipped: %s",
            total_runs,
            skipped_runs,
        )
        return

    if "," in args.site:
        sites_to_process = parse_sites_list(args.site)
        total_runs = len(sites_to_process)
        skipped_runs = 0

        for run_index, site in enumerate(sites_to_process, start=1):
            logger.info(
                "Processing year=%s day=%03d site=%s (%s/%s)",
                args.year,
                args.day_of_year,
                site,
                run_index,
                total_runs,
            )
            try:
                run_single_station(
                    workdir=workdir,
                    dia_path=dia_path,
                    exe_path=exe_path,
                    dat_path_obj=dat_path_obj,
                    output_dir=resolved_output_dir,
                    year=args.year,
                    day_of_year=args.day_of_year,
                    site=site,
                    elevation_cutoff=args.elevation_cutoff,
                    time_step_hours=args.time_step_hours,
                    correction_coefficient=args.correction_coefficient,
                    dry_run=args.dry_run,
                    runner=args.runner,
                    execution_timeout_seconds=args.execution_timeout_seconds,
                    organize_by_day=True,
                )
            except FileNotFoundError as exc:
                skipped_runs += 1
                logger.warning(
                    "Skipping year=%s day=%03d site=%s due to missing input files: %s",
                    args.year,
                    args.day_of_year,
                    site,
                    exc,
                )
            except ValueError as exc:
                skipped_runs += 1
                logger.warning(
                    "Skipping year=%s day=%03d site=%s due to invalid input data: %s",
                    args.year,
                    args.day_of_year,
                    site,
                    exc,
                )

            percent = round(run_index * 100 / total_runs)
            logger.info("Completed %s / %s", run_index, total_runs)
            logger.info("Progress: %s%%", percent)

        logger.info(
            "Multi-station run complete. Total station runs: %s, skipped: %s",
            total_runs,
            skipped_runs,
        )
        return

    run_single_station(
        workdir=workdir,
        dia_path=dia_path,
        exe_path=exe_path,
        dat_path_obj=dat_path_obj,
        output_dir=resolved_output_dir,
        year=args.year,
        day_of_year=args.day_of_year,
        site=args.site.strip(),
        elevation_cutoff=args.elevation_cutoff,
        time_step_hours=args.time_step_hours,
        correction_coefficient=args.correction_coefficient,
        dry_run=args.dry_run,
        runner=args.runner,
        execution_timeout_seconds=args.execution_timeout_seconds,
        organize_by_day=True,
    )
    logger.info("Completed 1 / 1")
    logger.info("Progress: 100%")


if __name__ == "__main__":
    main()