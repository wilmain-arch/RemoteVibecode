#!/usr/bin/env bash
set -euo pipefail
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/codex-phone-companion"
PAIR_FILE="$CONFIG_DIR/pairing-uri"
STATE_FILE="$CONFIG_DIR/state.json"
if [[ -s "$STATE_FILE" ]] && python3 - "$STATE_FILE" <<'PY'
import json, sys
try:
    assert json.load(open(sys.argv[1], encoding="utf-8")).get("paired")
except (OSError, ValueError, AssertionError):
    raise SystemExit(1)
PY
then
  echo "Телефон уже привязан. Открой приложение — чат появится автоматически."
  exit 0
fi
systemctl --user restart codex-phone-companion.service
sleep 1
if [[ ! -s "$PAIR_FILE" ]]; then
  echo "Код привязки не создан. Проверь службу codex-phone-companion." >&2
  exit 1
fi
echo "Открой приложение и нажми «Сканировать QR-код»:"
qrencode -t ANSIUTF8 < "$PAIR_FILE"
