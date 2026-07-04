# AbsTEC Suite

Utilities for preparing and running TayAbsTEC (`absolTEC.exe`) from tec-suite `.dat` inputs, plus helper scripts for batch launcher generation.

This repository currently focuses on operational run/launcher tooling only.

## What This Project Does

This workspace helps automate a Windows-based TayAbsTEC workflow:

1. Validates tec-suite input data for a station/day.
2. Rewrites `TayAbsTEC_24.04.17/absolTEC.dia` with runtime parameters.
3. Runs `TayAbsTEC_24.04.17/absolTEC.exe`.
4. Optionally generates one `.bat` launcher per station.

## Main Files

- `run_absoltec.py`: Main CLI runner. Updates `absolTEC.dia`, validates input `.dat` files, and launches `absolTEC.exe`.
- `generate_absoltec_launchers.py`: Generates per-station `.bat` launchers for a selected date.
- `run_absoltec.bat`: Example single-run Windows launcher.
- `docker-compose.dockur.yml`: Windows XP guest (dockur/windows, KVM) + dockur-mode runner — see [README.dockur.md](README.dockur.md).
- `dockur/oem/`: Provisioning scripts executed inside the XP guest (`install.bat`, `watcher.bat`).
- `tests/test_run_absoltec.py`: Unit tests for run and validation logic.
- `tests/test_dockur_runner.py`: Unit tests for the dockur job-dispatch protocol.
- `tests/test_generate_absoltec_launchers.py`: Unit tests for launcher generation.

Removed component:

- `absoltec_input_output_predictor.py` is no longer part of this project.

## Expected Data Layout

The scripts assume this input structure:

```text
in/
  YYYY/
    DDD/
      SITE/
        <site_prefix>_*_DDD_YY.dat
```

Examples:

- `in/2026/001/aksu/aksu_G01_001_26.dat`
- `in/2023/001/...`

`run_absoltec.py` resolves station folders with fallback matching (exact site, then prefix matching).

## Station Naming and Output Folder

The `SITE` value (passed via `--site` or the `SITE` environment variable) controls both input resolution and the final output folder name.

`absolTEC.exe` internally truncates the station name to its first 4 characters when naming its output folder, so it always creates `<YEAR>/<4-char-prefix>/` regardless of what was written to `absolTEC.dia`. For example, with `SITE=aksu0010` the executable produces `2026/aksu/`.

After `absolTEC.exe` finishes and results are moved to `--output-dir`, `run_absoltec.py` automatically renames that folder to the full `SITE` value and organizes it under the day folder:

```text
out/2026/aksu/  →  out/2026/001/aksu0010/
```

This means:

- Pass the full station identifier (e.g. `aksu0010`) in `SITE` — no manual renaming needed.
- If `SITE` is already 4 characters (e.g. `aksu`), no rename step occurs.
- If the destination folder (`out/2026/aksu0010/`) already exists, the rename is skipped to avoid overwriting previous results.

## Requirements

- Windows
- Python 3.10+ (3.11/3.12 recommended)
- Docker Desktop (optional, for containerized runs)
- TayAbsTEC binaries in `TayAbsTEC_24.04.17/`:
  - `absolTEC.exe`
  - `absolTEC.dia`

No third-party Python packages are required (standard library only).

## Quick Start

### 1. Run TayAbsTEC once

```powershell
python run_absoltec.py `
  --dat-path in `
  --year 2026 `
  --day-of-year 1 `
  --site aksu
```

Useful options:

- `--workdir TayAbsTEC_24.04.17`
- `--elevation-cutoff 10`
- `--time-step-hours 0.5`
- `--correction-coefficient 0.97`
- `--output-dir out` (move generated results from workdir into a dedicated folder)
- `--dry-run` (updates `.dia` only, skips `.exe` execution)

### 1b. Run all stations for multiple days

You can pass a day list and process every station folder found under each day:

```powershell
python run_absoltec.py `
  --dat-path in `
  --year 2026 `
  --days 001,002,003,004 `
  --output-dir out
```

`--days` also supports ranges, for example:

```powershell
python run_absoltec.py --dat-path in --year 2026 --days 001-365 --output-dir out
```

Batch mode behavior:

- `--days` and `--day-of-year` are mutually exclusive.
- When `--days` is used, stations are auto-discovered from `in/YYYY/DDD/*` and `--site` is not used.
- The script runs `absolTEC.exe` once per discovered station for each listed day.
- In batch mode, output is organized as `out/YYYY/DDD/STATION` (for example `out/2026/001/aksu0010`).

### 2. Generate launchers for all stations on a date

```powershell
python generate_absoltec_launchers.py `
  --dat-path in `
  --year 2026 `
  --day-of-year 1 `
  --output-dir launchers
```

Optional:

- `--stations aksu,alek,alex` (manual station list)
- `--stations-root in/2026/001` (custom discovery folder)
- `--python-exe C:\Python\Python312\python.exe`
- `--dry-run`

## Docker

This repository includes:

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

`TayAbsTEC_24.04.17` is copied into the image at build time and used as `/data/workdir` at runtime.

Build image:

```powershell
docker build --platform linux/amd64 -t abstec-suite:latest .
```

On Apple Silicon Macs, keep the image and container on `linux/amd64`. TayAbsTEC ships as a 32-bit x86 Windows executable, and native `arm64` Linux containers will not run it correctly under Wine.

By default, Docker Compose mounts a host output folder to `/data/out`:

```text
./out -> /data/out
```

`run_absoltec.py` can move generated results (for example `/data/workdir/<YEAR>`) into `/data/out` after execution.
You can change the host-side base folder with `ABSTEC_OUTPUT_DATA_PATH`, or override in-container destination with `OUTPUT_DIR`.

Docker Compose also lets you override the host input folder mounted to `/data/in`:

```text
ABSTEC_INPUT_DATA_PATH -> /data/in
```

Run one dry-run job (recommended in container):

```powershell
docker run --rm `
  -v "${PWD}\in:/data/in:ro" `
  -v "${PWD}\out:/data/out" `
  abstec-suite:latest `
  --workdir /data/workdir `
  --dat-path /data/in `
  --output-dir /data/out `
  --year 2026 `
  --day-of-year 1 `
  --site aksu `
  --dry-run
```

Run with Docker Compose:

```powershell
docker compose run --rm -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

Set DIA time step via Docker option `TIME_STEP_HOURS` (maps to `--time-step-hours`):

```powershell
docker compose run --rm -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu -e TIME_STEP_HOURS=0.5 abstec
```

`YEAR`, `DAY_OF_YEAR`, and `SITE` are read inside the container at runtime, so overriding them with `docker compose run -e ...` works as expected.

For batch mode (all stations for multiple days), pass `DAYS`:

```powershell
docker compose run --rm -e YEAR=2026 -e DAYS=001,002,003,004 -e DRY_RUN=0 abstec
```

`DAYS` also supports ranges:

```powershell
docker compose run --rm -e YEAR=2026 -e DAYS=001-365 -e DRY_RUN=0 abstec
```

When `DAYS` is set, the container passes `--days` to `run_absoltec.py` and does not pass `--day-of-year` or `--site`.

The Compose service is pinned to `linux/amd64` so Apple Silicon Macs run the Wine environment under Docker's x86_64 emulation instead of a native `arm64` container.

`RUNNER` is also configurable (`auto`, `wine`, `direct`, `dockur`). The default in this container is `wine`. The `dockur` runner dispatches execution to a real Windows XP guest instead of Wine — see [Windows XP runner](#windows-xp-runner-dockurwindows-kvm).

Dry run is enabled by default in Compose (`DRY_RUN=1`). Disable it with:

```powershell
docker compose run --rm -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

Set a max run time (seconds) to avoid indefinite hangs if Wine enters a debug/wait state:

```powershell
docker compose run --rm -e DRY_RUN=0 -e RUNNER=wine -e EXECUTION_TIMEOUT_SECONDS=900 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

If your station folders are named like `aksu0010`, pass the exact folder name in `SITE`. The output folder will be automatically renamed from the 4-char prefix (`aksu`) to the full name (`aksu0010`) — see [Station Naming and Output Folder](#station-naming-and-output-folder):

```powershell
docker compose run --rm -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu0010 abstec
```

For a single day, you can run multiple explicit stations by passing a comma-separated `SITE` list:

```powershell
docker compose run --rm -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=7 -e SITE=aksu0070,alks0070,bala0070 abstec
```

That runs stations one by one and writes separate output folders (for example `out/2026/007/aksu0070`, `out/2026/007/alks0070`, `out/2026/007/bala0070`).

Use a custom output location on the host:

```powershell
$env:ABSTEC_OUTPUT_DATA_PATH = "${PWD}\results"
docker compose run --rm -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

That will persist moved TayAbsTEC output under `results` on the host.

Use a custom input location on the host:

```powershell
$env:ABSTEC_INPUT_DATA_PATH = "D:\tec-suite\exports"
docker compose run --rm -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

That will mount `D:\tec-suite\exports` to `/data/in` in the container.

Important limitation:

- The provided image is Linux-based (`python:3.12.13-slim`).
- `absolTEC.exe` is a Windows executable; the image installs Wine so it can be executed from the Linux container.
- `absolTEC.exe` is a 32-bit Windows executable, so the image has to install 32-bit Wine support (`wine32:i386`) and must run as `linux/amd64` on Apple Silicon.
- On Docker Desktop for macOS, that package install can be memory-heavy during `docker build`.
- If the image build fails with `ResourceExhausted` or `cannot allocate memory`, increase Docker Desktop memory first. In practice, `6 GB` is a safer minimum and `8 GB` is more reliable.
- The first non-dry run can be slower while Wine initializes.
- If Wine is not desired, keep `DRY_RUN=1` and run `absolTEC.exe` directly on Windows.
- Some TayAbsTEC executable builds may still fail under Linux Wine with errors such as `ShellExecuteEx failed: Not enough memory` or `wine: failed to start ...`. In that case, treat container execution as unsupported for non-dry runs on that host and execute `absolTEC.exe` on native Windows — or use the [Windows XP runner](#windows-xp-runner-dockurwindows-kvm), which runs the exe on real Windows inside a KVM container on the same Linux host.

If you see this exact runtime error:

```text
Application could not be started, or no application associated with the specified file.
ShellExecuteEx failed: Not enough memory.
wine: failed to start L"Z:\\...\\absolTEC.exe"
```

it usually means Wine itself is installed but this specific TayAbsTEC build is not compatible with the current Linux Wine runtime (commonly seen on macOS Docker Desktop when running `linux/amd64` emulation for 32-bit Windows binaries).

Quick verification inside container:

```powershell
docker compose run --rm --entrypoint sh abstec -lc "wine --version && wine cmd /c echo ok"
```

If that command succeeds but `absolTEC.exe` still fails, use this split workflow:

1. Run container in `DRY_RUN=1` mode to validate inputs and rewrite `absolTEC.dia`.
2. Execute `absolTEC.exe` on native Windows for production runs.

If you need to execute `absolTEC.exe`, run `run_absoltec.py` directly on Windows (without `--dry-run`), or use the Windows XP runner below.

## Windows XP Runner (dockur/windows, KVM)

An alternative to Wine for unstable hosts: `absolTEC.exe` executes inside a real
Windows XP Professional guest running in a [dockur/windows](https://github.com/dockur/windows)
KVM container. The binary is a 32-bit PE built for OS 5.1, so XP is its native
platform and the whole class of Wine startup/runtime failures disappears.

Full documentation (architecture, job protocol, troubleshooting): [README.dockur.md](README.dockur.md).

Requirements:

- Linux host with `/dev/kvm` (bare metal, or a VM with nested virtualization).
- A Windows XP Professional license you are entitled to use (dockur installs
  with generic trial keys, not an activation license).
- `ABSTEC_INPUT_DATA_PATH_HOST` / `ABSTEC_OUTPUT_DATA_PATH_HOST` set in `.env`.

First-time bring-up (installs XP unattended, ~10-20 minutes — one time only):

```sh
docker compose -f docker-compose.dockur.yml up -d abstec-xp
```

Watch the installation at `http://<host>:8006`. When the guest desktop shows a
console window titled `abstec-watcher` reporting `watching W:\jobs`, the guest
is ready. The `dockur/oem/` scripts provision this automatically — no manual
steps inside the VM.

Run a single station (same env knobs as the Wine service, different compose file):

```sh
docker compose -f docker-compose.dockur.yml run --rm \
  -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu0010 abstec-dockur
```

Batch mode works the same way:

```sh
docker compose -f docker-compose.dockur.yml run --rm \
  -e DRY_RUN=0 -e YEAR=2026 -e DAYS=001-031 abstec-dockur
```

Or run the host side directly, without the runner container:

```sh
python run_absoltec.py --runner dockur \
  --dat-path /path/to/in --output-dir /path/to/out \
  --dockur-jobs-dir ./dockur/jobs \
  --year 2026 --day-of-year 1 --site aksu0010
```

How it differs from the Wine runner:

- Jobs are dispatched through a shared jobs folder (`dockur/jobs` on the host,
  `W:\jobs` inside the guest) — the guest watcher picks them up, runs the exe,
  and streams stdout back to `job.log`. Progress reporting and timeouts work
  exactly as with Wine; on timeout the guest force-terminates `absolTEC.exe`.
- The guest copies results into the shared out folder itself, so
  `--output-dir` (or `OUTPUT_DIR`) must point at the folder mounted to
  `/shared/out` — with the provided compose file this is already wired up.
- The XP guest persists in `dockur/storage/` (gitignored); `docker restart
  abstec-xp` also re-syncs the application folder into the guest after an
  `absolTEC.exe` update.
- Failed job folders are kept under `dockur/jobs/` for inspection; RDP is
  available on port `3390` (user `Docker`) for debugging inside the guest.

## Validation Rules in `run_absoltec.py`

Before running `absolTEC.exe`, the script checks:

- Station folder exists under `in/YYYY/DDD/SITE` (with fallback matching).
- Matching input files exist using pattern `<site_prefix>_*_DDD_YY.dat`.
- Matched files contain usable numeric data rows.
- TEC bias column (6th data column) is not entirely `0.000`.

If any check fails, it raises a clear error message to avoid running TayAbsTEC with bad input.

## Running Tests

From repo root:

```powershell
python -m unittest discover -s tests -v
```

## Notes

- `run_absoltec.py` overwrites `TayAbsTEC_24.04.17/absolTEC.dia` on each run.
- Use `--dry-run` first when validating new dates/stations.
- `run_absoltec.bat` is an editable example for operators who prefer batch execution.
