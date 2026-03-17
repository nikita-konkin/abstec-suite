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
- `tests/test_run_absoltec.py`: Unit tests for run and validation logic.
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
You can change the host-side base folder with `ABSTEC_OUTPUT_ROOT`, or override in-container destination with `OUTPUT_DIR`.

Docker Compose also lets you override the host input folder mounted to `/data/in`:

```text
ABSTEC_INPUT_ROOT -> /data/in
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

`YEAR`, `DAY_OF_YEAR`, and `SITE` are read inside the container at runtime, so overriding them with `docker compose run -e ...` works as expected.

The Compose service is pinned to `linux/amd64` so Apple Silicon Macs run the Wine environment under Docker's x86_64 emulation instead of a native `arm64` container.

`RUNNER` is also configurable (`auto`, `wine`, `direct`). The default in this container is `wine`.

Dry run is enabled by default in Compose (`DRY_RUN=1`). Disable it with:

```powershell
docker compose run --rm -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

Set a max run time (seconds) to avoid indefinite hangs if Wine enters a debug/wait state:

```powershell
docker compose run --rm -e DRY_RUN=0 -e RUNNER=wine -e EXECUTION_TIMEOUT_SECONDS=900 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

If your station folders are named like `aksu0010`, pass the exact folder name in `SITE`:

```powershell
docker compose run --rm -e DRY_RUN=0 -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu0010 abstec
```

Use a custom output location on the host:

```powershell
$env:ABSTEC_OUTPUT_ROOT = "${PWD}\results"
docker compose run --rm -e YEAR=2026 -e DAY_OF_YEAR=1 -e SITE=aksu abstec
```

That will persist moved TayAbsTEC output under `results` on the host.

Use a custom input location on the host:

```powershell
$env:ABSTEC_INPUT_ROOT = "D:\tec-suite\exports"
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
- Some TayAbsTEC executable builds may still fail under Linux Wine with errors such as `ShellExecuteEx failed: Not enough memory` or `wine: failed to start ...`. In that case, treat container execution as unsupported for non-dry runs on that host and execute `absolTEC.exe` on native Windows.

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

If you need to execute `absolTEC.exe`, run `run_absoltec.py` directly on Windows (without `--dry-run`).

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
