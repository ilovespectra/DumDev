#!/bin/sh
set -eu
cd "$(dirname "$0")"
export DUMDEV_DEVELOPER_MODE=1
exec python3 app.py
