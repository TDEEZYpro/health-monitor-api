#!/usr/bin/env bash
# Start the health-monitor API.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 main.py
