from __future__ import annotations

import argparse
import concurrent.futures
import errno
import hashlib
import logging
import os
import platform
import re
import signal
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path


logger = logging.getLogger(__name__)


class WineStartupFailureError(RuntimeError):
    """Raised when Wine fails to launch the Windows executable in the current runtime."""


PROGRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Example: "INFO: Completed 2618 / 15221"
    re.compile(r"Completed\s+(\d+)\s*/\s*(\d+)", flags=re.IGNORECASE),
    # Example: "INFO: Progress: 17%"
    re.compile(r"Progress:\s*(\d{1,3})\s*%", flags=re.IGNORECASE),
)

WINE_STARTUP_FAILURE_MARKERS: tuple[str, ...] = (
    "application could not be started, or no application associated with the specified file.",
    "shellexecuteex failed:",
    "shellexecuteex failed: not enough memory",
    "fork: resource temporarily unavailable",
    'failed to start l"z:',
    "c0000135",
)

WINE_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "wine",
        "wine64",
        "wine-preloader",
        "wine64-preloader",
        "wineserver",
        "winedevice",
        "winedevice.exe",
        "winedbg",
        "wineboot",
        "rpcss.exe",
        "services.exe",
        "plugplay.exe",
        "explorer.exe",
        "winemenubuilder.exe",
        "absoltec.exe",
    }
)


def _is_wine_process_name(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return Path(candidate).name.lower() in WINE_PROCESS_NAMES


def _extract_process_argv0(entry: Path) -> str:
    try:
        cmdline_bytes = (entry / "cmdline").read_bytes()
    except OSError:
        return ""

    for raw_part in cmdline_bytes.split(b"\x00"):
        if not raw_part:
            continue
        return raw_part.decode("utf-8", errors="replace")
    return ""

def _looks_like_wine_startup_failure(output_text: str) -> bool:
    lowered = output_text.lower()
    return any(marker in lowered for marker in WINE_STARTUP_FAILURE_MARKERS)


def _coerce_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _is_retryable_process_spawn_error(exc: OSError) -> bool:
    return exc.errno in {errno.EAGAIN, errno.ENOMEM}


def _list_lingering_wine_pids() -> list[int]:
    if platform.system() != "Linux":
        return []

    current_pid = os.getpid()
    matched_pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue

        pid = int(entry.name)
        if pid == current_pid:
            continue

        try:
            comm_text = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
        except OSError:
            comm_text = ""

        if _is_wine_process_name(comm_text):
            matched_pids.append(pid)
            continue

        if _is_wine_process_name(_extract_process_argv0(entry)):
            matched_pids.append(pid)

    return matched_pids


def _kill_lingering_wine_processes() -> int:
    killed = 0
    for pid in _list_lingering_wine_pids():
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (ProcessLookupError, PermissionError):
            continue
    return killed


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
    if runner in ("direct", "dockur"):
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


DAT_DATA_COLUMNS = 7
DAT_BIAS_COLUMN = 5


def _is_dat_header_line(stripped_line: str) -> bool:
    """True for tec-suite header lines that are not data.

    Most are '#'-prefixed, but the column-title line
    ("tsn, hour, el, az, tec.l1l2, tec.p1p2, validity") is not — it starts
    with a space, which is exactly the kind of text a fixed-format Fortran
    READ cannot convert.
    """
    if stripped_line.startswith("#"):
        return True
    lowered = stripped_line.lower()
    return lowered.startswith("tsn") and "hour" in lowered


def scan_dat_file(dat_file: Path) -> tuple[int, int, list[str]]:
    """Return (data_rows, nonzero_bias_rows, problems) for one tec-suite .dat file."""
    data_rows = 0
    nonzero_bias_rows = 0
    problems: list[str] = []

    text = dat_file.read_text(encoding="utf-8", errors="ignore")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped_line = raw_line.strip()
        if not stripped_line or _is_dat_header_line(stripped_line):
            continue

        parts = stripped_line.split()
        if len(parts) != DAT_DATA_COLUMNS:
            if len(problems) < 5:
                problems.append(
                    f"{dat_file.name}:{line_number}: expected {DAT_DATA_COLUMNS} columns, "
                    f"got {len(parts)}: {stripped_line[:60]!r}"
                )
            continue

        try:
            values = [float(part) for part in parts]
        except ValueError:
            if len(problems) < 5:
                problems.append(
                    f"{dat_file.name}:{line_number}: non-numeric field: {stripped_line[:60]!r}"
                )
            continue

        data_rows += 1
        if abs(values[DAT_BIAS_COLUMN]) > 1e-9:
            nonzero_bias_rows += 1

    return data_rows, nonzero_bias_rows, problems


def validate_dat_inputs(
    dat_path: Path,
    site: str,
    day_of_year: int,
    year: int,
    min_data_rows: int = 0,
    strict: bool = False,
) -> Path:
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

    checked_rows = 0
    nonzero_bias_values = 0
    problems: list[str] = []
    for dat_file in matching_files:
        rows, nonzero, file_problems = scan_dat_file(dat_file)
        checked_rows += rows
        nonzero_bias_values += nonzero
        problems.extend(file_problems[: max(0, 5 - len(problems))])

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

    if problems:
        # absolTEC parses these files with a fixed Fortran format, so a stray
        # non-numeric line makes it abort with "severe (64): input conversion
        # error" rather than skipping the row.
        detail = "; ".join(problems)
        if strict:
            raise ValueError(f"Malformed rows in tec-suite output: {detail}")
        logger.warning(
            "Malformed rows in tec-suite output for year=%s day=%03d site=%s "
            "(absolTEC may abort with a Fortran conversion error): %s",
            year,
            day_of_year,
            site,
            detail,
        )

    if min_data_rows and checked_rows < min_data_rows:
        raise ValueError(
            f"Only {checked_rows} usable data rows across {len(matching_files)} .dat file(s), "
            f"below --min-data-rows={min_data_rows}. The station has too little data "
            "for a meaningful absolTEC run."
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


def find_wineserver_binary(wine_path: str | None = None) -> str | None:
    wineserver_path = shutil.which("wineserver")
    if wineserver_path:
        return wineserver_path

    if wine_path:
        candidate = Path(wine_path).with_name("wineserver")
        if candidate.exists():
            return str(candidate)

    known_paths = [
        Path("/usr/bin/wineserver"),
        Path("/usr/lib/wine/wineserver"),
        Path("/usr/lib/wine/wineserver64"),
        Path("/usr/local/bin/wineserver"),
        Path("/opt/homebrew/bin/wineserver"),
    ]
    for candidate in known_paths:
        if candidate.exists():
            return str(candidate)

    return None


def reset_wine_runtime(*, wine_path: str | None = None) -> None:
    """Best-effort cleanup to mitigate long-run Wine resource leaks in containers."""
    if platform.system() != "Linux":
        return

    resolved_wine = wine_path or find_wine_binary()
    wineserver = find_wineserver_binary(resolved_wine)
    if not wineserver:
        logger.warning("Wine reset requested but wineserver binary was not found.")
        return

    force_killed = 0
    try:
        shutdown = subprocess.run(
            [wineserver, "-k"],
            check=False,
            capture_output=True,
            text=True,
        )
        wait_result = subprocess.run(
            [wineserver, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
        if shutdown.returncode != 0 or wait_result.returncode != 0:
            logger.warning(
                "Wine reset returned non-zero status (shutdown=%s, wait=%s). Forcing cleanup of lingering Wine processes.",
                shutdown.returncode,
                wait_result.returncode,
            )
            force_killed = _kill_lingering_wine_processes()
    except OSError as exc:
        logger.warning("Wine reset failed: %s", exc)
        force_killed = _kill_lingering_wine_processes()

    if force_killed:
        logger.warning("Force-killed %s lingering Wine process(es) during reset.", force_killed)

    # Give the kernel a moment to release pid/process-table pressure before the next launch.
    time.sleep(1.0)


def resolve_runner_command(exe_path: Path, runner: str) -> list[str]:
    if runner == "direct":
        return [str(exe_path)]

    if runner == "dockur":
        raise ValueError(
            "The dockur runner does not launch a local process. "
            "Jobs are dispatched through the shared jobs directory instead."
        )

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
        raise ValueError("--runner must be one of: auto, direct, wine, dockur")

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

    use_wine = should_use_wine(exe_path, runner)
    max_attempts = 2 if use_wine and platform.system() == "Linux" else 1

    for attempt in range(1, max_attempts + 1):
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(exe_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            if use_wine and platform.system() == "Linux" and _is_retryable_process_spawn_error(exc):
                if attempt < max_attempts:
                    logger.warning(
                        "Wine process spawn failed for absolTEC.exe (attempt %s/%s): %s. Resetting Wine runtime and retrying once.",
                        attempt,
                        max_attempts,
                        exc,
                    )
                    reset_wine_runtime(wine_path=command[0])
                    continue
                raise WineStartupFailureError(
                    "Wine process spawn failed for absolTEC.exe in the current Linux container runtime. "
                    f"Command: {' '.join(command)}. Error: {exc}"
                ) from exc
            raise

        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _kill_wine_process_group(proc)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout, stderr = exc.stdout, exc.stderr
            stdout = _coerce_process_output(stdout) or _coerce_process_output(exc.stdout)
            stderr = _coerce_process_output(stderr) or _coerce_process_output(exc.stderr)
            combined = f"{stdout}\n{stderr}".strip()
            if use_wine and _looks_like_wine_startup_failure(combined):
                if attempt < max_attempts:
                    logger.warning(
                        "Wine failed to start absolTEC.exe and then hung until timeout (attempt %s/%s). Resetting Wine runtime and retrying once.",
                        attempt,
                        max_attempts,
                    )
                    reset_wine_runtime(wine_path=command[0])
                    continue
                output_hint = ""
                if combined:
                    output_hint = f" Last output: {combined[-400:]}"
                raise WineStartupFailureError(
                    "Wine failed to start absolTEC.exe in the current Linux container runtime. "
                    f"Command: {' '.join(command)}."
                    f"{output_hint}"
                ) from exc
            output_hint = ""
            if combined:
                output_hint = f" Last output: {combined[-400:]}"
            raise RuntimeError(
                f"absolTEC did not finish within {timeout_seconds} seconds. "
                f"Command: {' '.join(command)}."
                f"{output_hint}"
            ) from exc
        stdout = _coerce_process_output(stdout)
        stderr = _coerce_process_output(stderr)

        if stdout:
            logger.info(stdout.rstrip("\n"))
        if stderr:
            logger.error(stderr.rstrip("\n"))

        combined_streams = "\n".join(part for part in (stdout, stderr) if part)
        progress_counter = extract_progress_counters(combined_streams)
        if progress_counter:
            logger.info("Progress counter: %s / %s", progress_counter[0], progress_counter[1])

        combined_output = f"{stdout}\n{stderr}"

        if proc.returncode == 0:
            if "error, no files" in combined_output.lower():
                raise RuntimeError("absolTEC reported 'Error, no files'. Check DAT_PATH and input files.")
            return

        if (
            use_wine
            and attempt < max_attempts
            and _looks_like_wine_startup_failure(combined_output)
        ):
            logger.warning(
                "Wine failed to start absolTEC.exe (attempt %s/%s). Resetting Wine runtime and retrying once.",
                attempt,
                max_attempts,
            )
            reset_wine_runtime(wine_path=command[0])
            continue

        base_error = (
            f"absolTEC process failed (return code {proc.returncode}). "
            f"Command: {' '.join(command)}"
        )
        if use_wine and _looks_like_wine_startup_failure(combined_output):
            base_error += (
                " Wine failed to start this executable in the current Linux container runtime. "
                "This is often a Wine compatibility issue or long-run Wine resource leak (not actual host RAM exhaustion). "
                "If this happens mid-batch, try enabling periodic Wine resets or run absolTEC.exe directly on Windows for production execution."
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
        if use_wine and _looks_like_wine_startup_failure(combined_output):
            raise WineStartupFailureError(base_error)
        raise RuntimeError(base_error)

    raise RuntimeError(
        "absolTEC execution failed after retries. "
        f"Command: {' '.join(command)}"
    )


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


def station_output_exists(
    output_dir: Path, year: int, day_of_year: int, site: str, organize_by_day: bool
) -> bool:
    """True when a previous run already produced results for this station/day.

    Used by --skip-existing so an interrupted batch can be restarted without
    redoing the stations it already completed.
    """
    if organize_by_day:
        destination = output_dir / str(year) / f"{day_of_year:03d}" / site
    else:
        destination = output_dir / str(year) / site
    return destination.is_dir() and any(destination.iterdir())


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


DOCKUR_DEFAULT_GUEST_DAT_PATH = "W:\\in\\"
DOCKUR_GUEST_WORKDIR = "C:\\absoltec\\work"
DOCKUR_GUEST_OUT_ROOT = "W:\\out"
DOCKUR_KILL_GRACE_SECONDS = 30.0

# Per-job staging folder under the shared output root. absolTEC always names
# its result folder after the 4-character site prefix, so every run used to
# land on the same <out>/<year>/<prefix> path: stations sharing a prefix
# overwrote each other, and with several jobs in flight a finishing job could
# pick up another station's folder. Staging each job separately removes both.
DOCKUR_STAGE_DIR_NAME = "_stage"
DOCKUR_GUEST_STAGE_ROOT = f"{DOCKUR_GUEST_OUT_ROOT}\\{DOCKUR_STAGE_DIR_NAME}"

# Written by the guest watcher so the host can tell how many jobs it will run
# concurrently (absent on pre-parallel watchers).
DOCKUR_WATCHER_STATUS_NAME = "_watcher.status"


def build_dockur_job_bat(year: int, job_name: str) -> str:
    """Batch script executed inside the Windows XP guest for a single station run.

    XP-era cmd only: no timeout.exe/robocopy, and redirections are written
    before `echo` so single-digit exit codes are not parsed as fd numbers.

    The watcher passes its slot workdir and per-slot executable name as %1/%2
    so concurrent jobs never share absolTEC.dia or the result folder. A
    pre-parallel watcher passes no arguments, so both fall back to the single
    shared workdir and this script keeps working unchanged.
    """
    lines = [
        "@echo off",
        "setlocal",
        'set "JOB=%~dp0"',
        'set "WORK=%~1"',
        'set "EXE=%~2"',
        f'if "%WORK%"=="" set "WORK={DOCKUR_GUEST_WORKDIR}"',
        'if "%EXE%"=="" set "EXE=absolTEC.exe"',
        f'set "YEAR={year}"',
        f'set "STAGE={DOCKUR_GUEST_STAGE_ROOT}\\{job_name}"',
        'if not exist "%WORK%\\%EXE%" (',
        '  >"%JOB%job.log" echo job.bat: %EXE% not found in %WORK%',
        '  >"%JOB%job.done" echo 3',
        "  goto :eof",
        ")",
        'copy /y "%JOB%absolTEC.dia" "%WORK%\\absolTEC.dia" >nul',
        # A previous job in this slot may have died before its results were
        # collected; shipping them out now would label them with this station.
        'if exist "%WORK%\\%YEAR%" rmdir /s /q "%WORK%\\%YEAR%"',
        'cd /d "%WORK%"',
        '"%EXE%" >"%JOB%job.log" 2>&1',
        'set "CODE=%ERRORLEVEL%"',
        'if exist "%WORK%\\%YEAR%" (',
        '  xcopy "%WORK%\\%YEAR%" "%STAGE%\\%YEAR%\\" /e /i /y >nul',
        '  rmdir /s /q "%WORK%\\%YEAR%"',
        ")",
        '>"%JOB%job.done" echo %CODE%',
        "endlocal",
    ]
    return "\r\n".join(lines) + "\r\n"


def read_dockur_watcher_slots(jobs_dir: Path) -> int | None:
    """Return the concurrency the guest watcher advertises, or None if unknown."""
    try:
        raw = (jobs_dir / DOCKUR_WATCHER_STATUS_NAME).read_text(
            encoding="ascii", errors="replace"
        )
    except OSError:
        return None
    match = re.search(r"slots\s*=\s*(\d+)", raw)
    if not match:
        return None
    slots = int(match.group(1))
    return slots if slots > 0 else None


def submit_dockur_job(jobs_dir: Path, dia_content: str, year: int, label: str) -> Path:
    """Create a job folder in the shared jobs directory and mark it ready.

    job.ready is written last so the guest watcher never picks up a
    half-written job over SMB.
    """
    jobs_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
    job_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_label}_{os.urandom(4).hex()}"
    job_dir = jobs_dir / job_name
    job_dir.mkdir()
    (job_dir / "absolTEC.dia").write_text(dia_content, encoding="utf-8")
    (job_dir / "job.bat").write_bytes(build_dockur_job_bat(year, job_name).encode("ascii"))
    (job_dir / "job.ready").write_text("ready\n", encoding="ascii")
    return job_dir


def _read_new_log_text(log_path: Path, offset: int) -> tuple[str, int]:
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(offset)
            chunk = log_file.read()
    except OSError:
        return "", offset
    if not chunk:
        return "", offset
    return chunk.decode("utf-8", errors="replace"), offset + len(chunk)


def _read_dockur_exit_code(
    done_path: Path,
    poll_seconds: float,
    visibility_timeout_seconds: float = 10.0,
) -> int:
    # The guest writes job.done in one small write, but allow a short window
    # for the content to become visible across the SMB/bind-mount boundary.
    # The file can transiently read as NUL bytes (size allocated, data not yet
    # flushed by the SMB server) — treat that the same as not-yet-written.
    deadline = time.monotonic() + visibility_timeout_seconds
    last_raw = ""
    while True:
        raw = ""
        try:
            raw = done_path.read_text(encoding="ascii", errors="replace")
        except OSError:
            pass
        last_raw = raw
        cleaned = raw.replace("\x00", "").strip()
        if cleaned:
            try:
                return int(cleaned)
            except ValueError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Unreadable exit code in {done_path}: {raw!r}. "
                        "Inspect the job folder inside the shared jobs directory."
                    ) from None
        elif time.monotonic() > deadline:
            raise RuntimeError(
                f"job.done appeared but stayed empty/unflushed: {done_path} "
                f"(last read: {last_raw!r})"
            )
        time.sleep(poll_seconds)


def wait_for_dockur_job(
    job_dir: Path,
    timeout_seconds: float | None,
    poll_seconds: float | None = None,
    kill_grace_seconds: float = DOCKUR_KILL_GRACE_SECONDS,
) -> tuple[int, str]:
    """Wait for the XP guest to finish a job, streaming job.log into the logger.

    Returns (exit_code, combined_log_text). On timeout a job.kill flag is
    written so the guest watcher force-terminates absolTEC.exe.
    """
    if poll_seconds is None:
        poll_seconds = float(os.environ.get("DOCKUR_POLL_SECONDS", "2"))
    log_path = job_dir / "job.log"
    done_path = job_dir / "job.done"
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    log_offset = 0
    collected: list[str] = []

    def drain_log() -> None:
        nonlocal log_offset
        text, log_offset = _read_new_log_text(log_path, log_offset)
        if text:
            collected.append(text)
            for line in text.splitlines():
                if line.strip():
                    logger.info("[absolTEC] %s", line.rstrip())

    while not done_path.exists():
        drain_log()
        if deadline is not None and time.monotonic() > deadline:
            (job_dir / "job.kill").write_text("timeout\n", encoding="ascii")
            kill_deadline = time.monotonic() + kill_grace_seconds
            while time.monotonic() < kill_deadline and not done_path.exists():
                time.sleep(poll_seconds)
            drain_log()
            raise RuntimeError(
                f"absolTEC did not finish within {timeout_seconds} seconds in the XP guest "
                f"(job {job_dir.name}). A kill flag was sent to the guest watcher. "
                "Job folder kept for inspection."
            )
        time.sleep(poll_seconds)

    drain_log()
    exit_code = _read_dockur_exit_code(done_path, poll_seconds)
    combined = "".join(collected)
    progress_counter = extract_progress_counters(combined)
    if progress_counter:
        logger.info("Progress counter: %s / %s", progress_counter[0], progress_counter[1])
    return exit_code, combined


def _wait_for_watcher_ack(job_dir: Path, grace_seconds: float, poll_seconds: float = 0.5) -> bool:
    """Wait until the guest watcher acknowledges job completion.

    The watcher deletes job.running after it has seen job.done. Removing the
    job folder before that leaves the watcher polling a nonexistent path
    forever, stalling the whole queue — so the folder may only be cleaned up
    once job.running is gone.
    """
    running_path = job_dir / "job.running"
    deadline = time.monotonic() + grace_seconds
    while running_path.exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(poll_seconds)
    return True


def collect_dockur_stage_output(
    output_dir: Path,
    job_name: str,
    year: int,
    day_of_year: int,
    site: str,
    organize_by_day: bool,
) -> Path | None:
    """Move one job's staged guest output into the final output layout.

    The guest writes results to <output_dir>/_stage/<job>/<year>/<prefix>, so
    the folder belonging to this run is identified by job name rather than by
    globbing the shared year directory. That is what makes concurrent jobs —
    and stations sharing a 4-character prefix — safe.
    """
    job_stage_dir = output_dir / DOCKUR_STAGE_DIR_NAME / job_name
    stage_year_dir = job_stage_dir / str(year)
    if not stage_year_dir.is_dir():
        return None

    produced = sorted(path for path in stage_year_dir.iterdir() if path.is_dir())
    if not produced:
        shutil.rmtree(job_stage_dir, ignore_errors=True)
        return None

    if organize_by_day:
        destination = output_dir / str(year) / f"{day_of_year:03d}" / site
    else:
        destination = output_dir / str(year) / site

    for index, source in enumerate(produced):
        # absolTEC emits exactly one folder per run; keep any extras rather
        # than silently dropping them.
        target = destination if index == 0 else destination.parent / f"{site}_{source.name}"
        _move_with_merge(source, target)

    shutil.rmtree(job_stage_dir, ignore_errors=True)
    return destination


def run_absoltec_dockur(
    *,
    jobs_dir: Path,
    dia_content: str,
    year: int,
    label: str,
    timeout_seconds: float | None,
    ack_grace_seconds: float = 15.0,
) -> str:
    job_dir = submit_dockur_job(jobs_dir, dia_content, year, label)
    logger.info("Submitted dockur job: %s", job_dir.name)
    exit_code, output = wait_for_dockur_job(job_dir, timeout_seconds)

    if exit_code == 0 and "error, no files" in output.lower():
        raise RuntimeError(
            "absolTEC reported 'Error, no files' in the XP guest. "
            "Check that the guest dat path (absolTEC.dia line 1) points at the "
            "shared input folder and that input files exist for this station."
        )
    if exit_code != 0:
        output_hint = ""
        tail = output.strip()[-400:]
        if tail:
            output_hint = f" Last output: {tail}"
        raise RuntimeError(
            f"absolTEC exited with code {exit_code} in the XP guest (job {job_dir.name})."
            f"{output_hint} Job folder kept for inspection."
        )

    if _wait_for_watcher_ack(job_dir, ack_grace_seconds):
        shutil.rmtree(job_dir, ignore_errors=True)
    else:
        logger.warning(
            "Guest watcher did not acknowledge job %s within %.0fs; "
            "leaving the job folder in place.",
            job_dir.name,
            ack_grace_seconds,
        )
    return job_dir.name


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
    dockur_jobs_dir: Path | None = None,
    dockur_guest_dat_path: str = DOCKUR_DEFAULT_GUEST_DAT_PATH,
    min_data_rows: int = 0,
    strict_dat_validation: bool = False,
    skip_existing: bool = False,
) -> str:
    if skip_existing and output_dir and station_output_exists(
        output_dir, year, day_of_year, site, organize_by_day
    ):
        logger.info(
            "Skipping year=%s day=%03d site=%s: output already present.",
            year,
            day_of_year,
            site,
        )
        return "skipped-existing"

    validate_dat_inputs(
        dat_path_obj,
        site,
        day_of_year,
        year,
        min_data_rows=min_data_rows,
        strict=strict_dat_validation,
    )

    dat_path_for_dia = str(dat_path_obj)
    if runner == "dockur":
        dat_path_for_dia = dockur_guest_dat_path
    elif should_use_wine(exe_path, runner):
        dat_path_for_dia = to_wine_windows_path(str(dat_path_obj))

    dia_content = build_dia_content(
        dat_path=dat_path_for_dia,
        elevation_cutoff=elevation_cutoff,
        year=year,
        day_of_year=day_of_year,
        site=site,
        time_step_hours=time_step_hours,
        correction_coefficient=correction_coefficient,
    )
    if runner == "dockur":
        # Every dockur job carries its own absolTEC.dia inside the job folder,
        # so the shared workdir copy is unused — and writing it here would race
        # between concurrent jobs.
        logger.info(
            "Prepared absolTEC.dia for year=%s day=%03d site=%s: %s",
            year,
            day_of_year,
            site,
            dia_content.replace("\n", " | ").strip(" |"),
        )
    else:
        dia_path.write_text(dia_content, encoding="utf-8")
        logger.info("Updated: %s", dia_path)

    if dry_run:
        logger.info("Dry run enabled. Skipping absolTEC.exe execution.")
        return "dry-run"

    timeout_seconds: float | None = execution_timeout_seconds
    if timeout_seconds is not None and timeout_seconds <= 0:
        timeout_seconds = None

    if runner == "dockur":
        if dockur_jobs_dir is None:
            raise ValueError("--dockur-jobs-dir is required when --runner dockur is used")
        job_name = run_absoltec_dockur(
            jobs_dir=dockur_jobs_dir,
            dia_content=dia_content,
            year=year,
            label=f"{year}_{day_of_year:03d}_{site}",
            timeout_seconds=timeout_seconds,
        )
        logger.info("Finished dockur job: year=%s day=%03d site=%s", year, day_of_year, site)
        # The guest stages workdir\<year> under <out>\_stage\<job>, so this run's
        # results are picked up by job name instead of by scanning the shared
        # year folder that every other job also writes into.
        if output_dir:
            organized = collect_dockur_stage_output(
                output_dir, job_name, year, day_of_year, site, organize_by_day
            )
            if organized:
                logger.info("Organized station output under day folder: %s", organized)
            else:
                logger.warning(
                    "absolTEC produced no output for year=%s day=%03d site=%s "
                    "(nothing staged under %s). The guest run itself succeeded, so "
                    "either the station's DAT input yielded no usable arcs, or the "
                    "XP VM's /shared/out mount does not point at --output-dir.",
                    year,
                    day_of_year,
                    site,
                    Path(output_dir) / DOCKUR_STAGE_DIR_NAME / job_name,
                )
        return "ok"

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

    return "ok"


def execute_station_runs(
    tasks: list[tuple[int, str]],
    run_station: Callable[[int, str], str],
    *,
    year: int,
    jobs: int,
    max_consecutive_failures: int,
    wine_reset_every_runs: int = 0,
    reset_wine: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Run every (day_of_year, site) task, skipping failures instead of aborting.

    A station that makes absolTEC exit non-zero used to kill the whole batch:
    the runner raises a plain RuntimeError, which the old loop did not catch
    (it handled only FileNotFoundError/ValueError/WineStartupFailureError). One
    malformed .dat therefore ended an 11k-station run at 15%.

    Failures are still not ignored: `max_consecutive_failures` in a row aborts
    the batch, because that pattern means something systemic is broken rather
    than one station having bad data.

    Returns (total_runs, skipped_runs).
    """
    total_runs = len(tasks)
    state_lock = threading.Lock()
    completed = 0
    skipped = 0
    consecutive_failures = 0
    abort_reason: list[str] = []

    def record(
        day_of_year: int,
        site: str,
        error: BaseException | None,
        reason: str = "",
        status: str = "ok",
    ) -> None:
        nonlocal completed, skipped, consecutive_failures
        with state_lock:
            completed += 1
            if error is None:
                consecutive_failures = 0
                if status == "skipped-existing":
                    skipped += 1
            else:
                skipped += 1
                consecutive_failures += 1
                logger.warning(
                    "Skipping year=%s day=%03d site=%s due to %s: %s",
                    year,
                    day_of_year,
                    site,
                    reason,
                    error,
                )
                if (
                    max_consecutive_failures
                    and consecutive_failures >= max_consecutive_failures
                    and not abort_reason
                ):
                    abort_reason.append(f"{consecutive_failures} consecutive station failures")
            logger.info("Completed %s / %s", completed, total_runs)
            logger.info("Progress: %s%%", round(completed * 100 / total_runs))

    def execute(index: int, day_of_year: int, site: str) -> None:
        if abort_reason:
            return
        logger.info(
            "Processing year=%s day=%03d site=%s (%s/%s)",
            year,
            day_of_year,
            site,
            index,
            total_runs,
        )
        try:
            status = run_station(day_of_year, site)
        except FileNotFoundError as exc:
            record(day_of_year, site, exc, "missing input files")
        except ValueError as exc:
            record(day_of_year, site, exc, "invalid input data")
        except WineStartupFailureError as exc:
            # Must precede RuntimeError: WineStartupFailureError subclasses it.
            record(day_of_year, site, exc, "Wine startup/runtime failure")
            if reset_wine is not None:
                reset_wine()
        except RuntimeError as exc:
            # absolTEC crashed or timed out for this station — e.g. the Fortran
            # "severe (64): input conversion error" raised by a malformed row.
            record(day_of_year, site, exc, "absolTEC runtime failure")
        else:
            record(day_of_year, site, None, status=status or "ok")

    if jobs <= 1:
        for index, (day_of_year, site) in enumerate(tasks, start=1):
            if (
                wine_reset_every_runs
                and index > 1
                and (index - 1) % wine_reset_every_runs == 0
                and reset_wine is not None
            ):
                logger.info(
                    "Resetting Wine runtime after %s station runs to avoid long-batch failures.",
                    index - 1,
                )
                reset_wine()
            execute(index, day_of_year, site)
            if abort_reason:
                break
    else:
        logger.info("Running up to %s station jobs concurrently.", jobs)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=jobs, thread_name_prefix="station"
        ) as pool:
            futures = [
                pool.submit(execute, index, day_of_year, site)
                for index, (day_of_year, site) in enumerate(tasks, start=1)
            ]
            try:
                for future in concurrent.futures.as_completed(futures):
                    # execute() handles its own errors; this re-raises only if
                    # the runner itself has a bug worth surfacing loudly.
                    future.result()
                    if abort_reason:
                        break
            finally:
                if abort_reason:
                    for future in futures:
                        future.cancel()

    if abort_reason:
        raise RuntimeError(
            f"Aborting batch after {abort_reason[0]}. That points at something systemic "
            "(guest watcher stopped, shared mounts, or absolTEC.dia paths) rather than "
            f"per-station bad data. Completed {completed} of {total_runs} runs."
        )

    return total_runs, skipped


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
        choices=["auto", "direct", "wine", "dockur"],
        default="auto",
        help=(
            "How to launch absolTEC executable (default: auto). "
            "'dockur' dispatches jobs to a Windows XP guest running in a "
            "dockur/windows KVM container via a shared jobs directory."
        ),
    )
    parser.add_argument(
        "--dockur-jobs-dir",
        default=os.environ.get("DOCKUR_JOBS_DIR"),
        help=(
            "Host path of the jobs directory shared with the XP guest "
            "(required for --runner dockur; defaults to DOCKUR_JOBS_DIR env)."
        ),
    )
    parser.add_argument(
        "--dockur-guest-dat-path",
        default=os.environ.get("DOCKUR_GUEST_DAT_PATH", DOCKUR_DEFAULT_GUEST_DAT_PATH),
        help=(
            "Input data root as seen from inside the XP guest, written to "
            f"absolTEC.dia line 1 (default: {DOCKUR_DEFAULT_GUEST_DAT_PATH!r})."
        ),
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
    parser.add_argument(
        "--jobs",
        type=int,
        default=int(os.environ.get("ABSTEC_JOBS", "1")),
        help=(
            "Number of stations to process concurrently (default: 1). Only the "
            "'dockur' runner supports values above 1, and the XP guest watcher must "
            "expose at least this many slots (set the number in <jobs dir>/_slots.cfg "
            "and restart the guest)."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip stations whose output folder already exists and is non-empty, so an "
            "interrupted batch can be restarted without redoing completed stations."
        ),
    )
    parser.add_argument(
        "--min-data-rows",
        type=int,
        default=int(os.environ.get("ABSTEC_MIN_DATA_ROWS", "0")),
        help=(
            "Skip a station when its matched .dat files hold fewer than this many "
            "usable data rows in total (default: 0 = no minimum)."
        ),
    )
    parser.add_argument(
        "--strict-dat-validation",
        action="store_true",
        help=(
            "Treat malformed .dat rows as a reason to skip the station instead of "
            "only warning. absolTEC reads these files with a fixed Fortran format and "
            "aborts on a stray non-numeric line."
        ),
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=int(os.environ.get("ABSTEC_MAX_CONSECUTIVE_FAILURES", "25")),
        help=(
            "Abort the batch after this many stations fail in a row, which indicates a "
            "systemic problem rather than bad data (default: 25; 0 disables the check)."
        ),
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
    use_wine = should_use_wine(exe_path, args.runner)

    dockur_jobs_dir: Path | None = None
    if args.runner == "dockur":
        if not args.dockur_jobs_dir:
            raise ValueError(
                "--runner dockur requires --dockur-jobs-dir (or the DOCKUR_JOBS_DIR environment variable)"
            )
        dockur_jobs_dir = Path(args.dockur_jobs_dir).resolve()
        if not args.dry_run and not dockur_jobs_dir.exists():
            raise FileNotFoundError(
                f"Dockur jobs directory not found: {dockur_jobs_dir}. "
                "Is the shared jobs volume mounted (see docker-compose.dockur.yml)?"
            )

    jobs = max(1, args.jobs)
    if jobs > 1:
        if args.runner != "dockur":
            raise ValueError(
                f"--jobs {jobs} is only supported with --runner dockur. The wine/direct "
                "runners share one workdir and one absolTEC.dia, so concurrent runs would "
                "overwrite each other's inputs and results."
            )
        if not args.dry_run and dockur_jobs_dir is not None:
            slots = read_dockur_watcher_slots(dockur_jobs_dir)
            if slots is None:
                logger.warning(
                    "The XP guest watcher does not report its slot count, so it is "
                    "probably the older serial version: jobs will queue and run one at "
                    "a time regardless of --jobs %s. Update dockur/oem/watcher.bat in "
                    "the guest to enable real concurrency.",
                    jobs,
                )
            elif slots < jobs:
                logger.warning(
                    "The XP guest watcher exposes %s slot(s) but --jobs is %s; the extra "
                    "jobs will simply queue. Raise the number in <jobs dir>/_slots.cfg "
                    "(and the guest's CPU_CORES), then restart the guest.",
                    slots,
                    jobs,
                )

    def run_station(day_of_year: int, site: str) -> str:
        return run_single_station(
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
            dockur_jobs_dir=dockur_jobs_dir,
            dockur_guest_dat_path=args.dockur_guest_dat_path,
            min_data_rows=args.min_data_rows,
            strict_dat_validation=args.strict_dat_validation,
            skip_existing=args.skip_existing,
        )

    if args.days:
        station_run_plan = build_station_run_plan(dat_path_obj, args.year, days_to_process)
        total_runs = len(station_run_plan)
        wine_reset_every_runs = 0
        should_reset_wine = use_wine and platform.system() == "Linux" and not args.dry_run
        if should_reset_wine:
            if total_runs >= 500 or len(days_to_process) > 7:
                wine_reset_every_runs = int(os.environ.get("WINE_RESET_EVERY_RUNS", "200"))
                wine_reset_every_runs = max(0, wine_reset_every_runs)

        total_runs, skipped_runs = execute_station_runs(
            station_run_plan,
            run_station,
            year=args.year,
            jobs=jobs,
            max_consecutive_failures=args.max_consecutive_failures,
            wine_reset_every_runs=wine_reset_every_runs,
            reset_wine=reset_wine_runtime if should_reset_wine else None,
        )
        logger.info(
            "Batch run complete. Total station runs: %s, skipped: %s",
            total_runs,
            skipped_runs,
        )
        return

    if "," in args.site:
        sites_to_process = parse_sites_list(args.site)
        total_runs = len(sites_to_process)
        wine_reset_every_runs = 0
        should_reset_wine = use_wine and platform.system() == "Linux" and not args.dry_run
        if should_reset_wine and total_runs >= 500:
            wine_reset_every_runs = int(os.environ.get("WINE_RESET_EVERY_RUNS", "200"))
            wine_reset_every_runs = max(0, wine_reset_every_runs)

        total_runs, skipped_runs = execute_station_runs(
            [(args.day_of_year, site) for site in sites_to_process],
            run_station,
            year=args.year,
            jobs=jobs,
            max_consecutive_failures=args.max_consecutive_failures,
            wine_reset_every_runs=wine_reset_every_runs,
            reset_wine=reset_wine_runtime if should_reset_wine else None,
        )
        logger.info(
            "Multi-station run complete. Total station runs: %s, skipped: %s",
            total_runs,
            skipped_runs,
        )
        return

    run_station(args.day_of_year, args.site.strip())
    logger.info("Completed 1 / 1")
    logger.info("Progress: 100%")


if __name__ == "__main__":
    main()
