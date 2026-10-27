#!/usr/bin/env bash
# Eenmalige inrichting van CinemaViewer op Linux (Mint/Ubuntu).
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "python3 ontbreekt (sudo apt install python3-venv)."; exit 1; }

if [ ! -x ".venv-linux/bin/python" ]; then
    python3 -m venv .venv-linux
fi
.venv-linux/bin/pip install --upgrade pip
.venv-linux/bin/pip install -r requirements.txt

if command -v apt-get >/dev/null; then
    missing=()
    ldconfig -p | grep -q libvlc.so || missing+=(vlc)
    command -v xdotool >/dev/null || missing+=(xdotool)
    command -v xinput  >/dev/null || missing+=(xinput)
    command -v pamtester >/dev/null || missing+=(pamtester)
    if [ ${#missing[@]} -gt 0 ]; then
        echo ""
        echo "Aanbevolen systeemhulpmiddelen ontbreken nog (nodig voor afspelen/kiosk):"
        echo "  sudo apt install ${missing[*]}"
    fi
fi

echo ""
echo "Klaar. Start met: ./run.sh"
