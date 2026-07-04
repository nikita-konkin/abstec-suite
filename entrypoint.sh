#!/bin/sh
set -e

RUNNER="${RUNNER:-wine}"

# Wine and the virtual display are only needed for local wine execution;
# the dockur runner dispatches jobs to the Windows XP guest instead.
if [ "$RUNNER" != "dockur" ]; then
	# Start virtual display
	Xvfb :0 -screen 0 1024x768x16 -ac &
	XVFB_PID=$!

	# Give it a moment to start
	sleep 1

	# Run the app with DISPLAY set
	export DISPLAY=:0
	export WINEPREFIX="${WINEPREFIX:-/wine}"
	export WINEARCH="${WINEARCH:-win32}"
	export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-root}"

	if command -v wine >/dev/null 2>&1; then
		WINE_BIN="wine"
	elif command -v wine64 >/dev/null 2>&1; then
		WINE_BIN="wine64"
	else
		echo "Error: Wine is not installed in this container." >&2
		exit 1
	fi

	"$WINE_BIN" --version >/dev/null 2>&1 || {
		echo "Error: Wine is installed but not runnable in this container." >&2
		exit 1
	}

	mkdir -p "$WINEPREFIX" "$XDG_RUNTIME_DIR"
	chmod 700 "$XDG_RUNTIME_DIR"

	# Initialize Wine prefix at runtime (more reliable than doing it during image build).
	if [ ! -f "$WINEPREFIX/system.reg" ]; then
		wineboot --init
		wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Debugger /t REG_SZ /d "" /f
		wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Auto /t REG_SZ /d "1" /f
	fi
fi

if [ "$#" -gt 0 ]; then
	exec python /app/run_absoltec.py "$@"
fi

YEAR="${YEAR:-2026}"
DAYS="${DAYS:-}"
DAY_OF_YEAR="${DAY_OF_YEAR:-1}"
SITE="${SITE:-aksu}"
TIME_STEP_HOURS="${TIME_STEP_HOURS:-0.5}"
RUNNER="${RUNNER:-wine}"
EXECUTION_TIMEOUT_SECONDS="${EXECUTION_TIMEOUT_SECONDS:-900}"
DRY_RUN="${DRY_RUN:-1}"
OUTPUT_DIR="${OUTPUT_DIR:-/data/out}"

set -- \
	--workdir /data/workdir \
	--dat-path /data/in \
	--year "$YEAR" \
	--time-step-hours "$TIME_STEP_HOURS" \
	--runner "$RUNNER" \
	--execution-timeout-seconds "$EXECUTION_TIMEOUT_SECONDS" \
	--output-dir "$OUTPUT_DIR"

if [ -n "$DAYS" ]; then
	set -- "$@" --days "$DAYS"
else
	set -- "$@" --day-of-year "$DAY_OF_YEAR" --site "$SITE"
fi

if [ "$RUNNER" = "dockur" ]; then
	set -- "$@" --dockur-jobs-dir "${DOCKUR_JOBS_DIR:-/data/jobs}"
	if [ -n "$DOCKUR_GUEST_DAT_PATH" ]; then
		set -- "$@" --dockur-guest-dat-path "$DOCKUR_GUEST_DAT_PATH"
	fi
fi

case "$(printf '%s' "$DRY_RUN" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes)
		set -- "$@" --dry-run
		;;
esac

exec python /app/run_absoltec.py "$@"