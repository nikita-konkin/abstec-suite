#!/bin/sh
set -e

# Start virtual display
Xvfb :0 -screen 0 1024x768x16 -ac &
XVFB_PID=$!

# Give it a moment to start
sleep 1

# Run the app with DISPLAY set
export DISPLAY=:0
exec python /app/run_absoltec.py "$@"