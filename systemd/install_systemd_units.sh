#!/usr/bin/env bash
# Install Veris pipeline systemd units.
#
# Copies the 9 unit files from this repo's systemd/ directory into
# /etc/systemd/system/, runs daemon-reload, then STOPS.
#
# Does NOT enable any timers. Does NOT touch veris.service.
# Enable timers manually after review.
#
# Usage:
#     bash systemd/install_systemd_units.sh
#
# Expected output: one line per file copied, then "daemon-reload complete".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=/etc/systemd/system

if [ "$EUID" -ne 0 ]; then
    echo "Need sudo: re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

cd "$SCRIPT_DIR"

for unit in veris-fetch.service veris-fetch.timer \
            veris-extract.service \
            veris-load.service \
            veris-priority.service \
            veris-preverify.service \
            veris-backfill.service \
            veris-verdicts.service veris-verdicts.timer; do
    if [ ! -f "$unit" ]; then
        echo "MISSING: $unit (in $SCRIPT_DIR)"
        exit 1
    fi
    cp -v "$unit" "$TARGET/$unit"
done

systemctl daemon-reload
echo ""
echo "daemon-reload complete"
echo ""
echo "Files installed but NOTHING is enabled yet. Next steps:"
echo ""
echo "  # 1. Smoke test a single stage via systemd:"
echo "  sudo systemctl start veris-fetch.service"
echo "  sudo journalctl -u veris-fetch.service -f   # Ctrl+C to detach"
echo ""
echo "  # 2. If clean, enable timers:"
echo "  sudo systemctl enable --now veris-fetch.timer"
echo "  sudo systemctl enable --now veris-verdicts.timer"
echo ""
echo "  # 3. Monitor 1-2 cycles, then disable old daemon:"
echo "  sudo systemctl stop veris.service"
echo "  sudo systemctl disable veris.service"
