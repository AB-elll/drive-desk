#!/bin/bash
# DriveDesk 起動スクリプト
# launchd / 手動実行の両方で使う

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_ID="${1:-karas}"

# .env 読み込み
if [ -f "$SCRIPT_DIR/clients/$CLIENT_ID/.env" ]; then
    set -a
    source "$SCRIPT_DIR/clients/$CLIENT_ID/.env"
    set +a
fi

# venv 有効化
source "$SCRIPT_DIR/.venv/bin/activate"

cd "$SCRIPT_DIR/src"
exec python main.py "$CLIENT_ID"
