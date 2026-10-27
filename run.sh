#!/usr/bin/env bash
# CinemaViewer launcher voor Linux (Mint/Ubuntu). Zie setup.sh voor de eenmalige inrichting.
set -e
cd "$(dirname "$0")"

PY=".venv-linux/bin/python"
if [ ! -x "$PY" ]; then
    echo "CinemaViewer wordt voor het eerst ingericht op deze computer..."
    ./setup.sh
fi

exec "$PY" CinemaViewer.pyw "$@"
