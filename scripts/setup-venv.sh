#!/usr/bin/env bash
# Debian/Ubuntu: при отсутствии venv — apt install python3-venv python3-full
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
./.venv/bin/pip install -U pip wheel
./.venv/bin/pip install -r requirements.txt
echo "Готово. Запуск:"
echo "  source .venv/bin/activate"
echo "  uvicorn main:app --host 127.0.0.1 --port 8765"
