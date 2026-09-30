#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
THREAD_ID="${1:?Usage: start-bridge.sh THREAD_ID}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/codex-phone-companion"
CERT_DIR="$CONFIG_DIR/tls"
CERT_FILE="$CERT_DIR/bridge.crt"
KEY_FILE="$CERT_DIR/bridge.key"
STATE_FILE="$CONFIG_DIR/state.json"
PAIR_FILE="$CONFIG_DIR/pairing-uri"
mkdir -p "$CERT_DIR"
chmod 700 "$CONFIG_DIR" "$CERT_DIR"

LAN_INTERFACE="$(ip -o -4 route show default | awk '{for (i=1;i<=NF;i++) if ($i=="dev" && $(i+1)!="tailscale0") {print $(i+1); exit}}')"
SERVER_IP="$(ip -o -4 addr show dev "$LAN_INTERFACE" scope global 2>/dev/null | awk '{split($4, parts, "/"); print parts[1]; exit}')"
if [[ -z "$SERVER_IP" ]]; then
  echo "Не удалось определить LAN-адрес ПК." >&2
  exit 1
fi

if [[ ! -s "$CERT_FILE" || ! -s "$KEY_FILE" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -keyout "$KEY_FILE" -out "$CERT_FILE" \
    -subj "/CN=Codex Phone Companion" \
    -addext "subjectAltName=IP:$SERVER_IP,IP:127.0.0.1,DNS:localhost" \
    >/dev/null 2>&1
  chmod 600 "$KEY_FILE" "$CERT_FILE"
fi

FINGERPRINT="$(openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256 | cut -d= -f2)"
PAIR_PIN="$(python3 -c 'import secrets; print(f"{secrets.randbelow(100000000):08d}")')"
TAILSCALE_IP=""
if command -v tailscale >/dev/null 2>&1; then
  TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
fi
PAIR_URI="$(python3 - "$SERVER_IP" "$TAILSCALE_IP" "$PAIR_PIN" "$FINGERPRINT" <<'PY'
import sys
from urllib.parse import urlencode
lan, tail, pin, fingerprint = sys.argv[1:]
params = {"host": f"https://{lan}:8765", "pin": pin, "fingerprint": fingerprint}
if tail:
    params["tailHost"] = f"https://{tail}:8765"
print("codexphone://pair?" + urlencode(params))
PY
)"
PAIRED="$(python3 - "$STATE_FILE" "$THREAD_ID" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print("yes" if data.get("paired") and data.get("thread_id") == sys.argv[2] else "no")
except (OSError, ValueError):
    print("no")
PY
)"
printf '%s\n' "$PAIR_URI" > "$PAIR_FILE"
chmod 600 "$PAIR_FILE"
echo
echo "RemoteVibecode"
if [[ "$PAIRED" == "yes" ]]; then
  echo "Телефон уже привязан. Открой приложение — чат появится автоматически."
else
  echo "При первой установке открой приложение и сканируй QR-код на ПК."
  if [[ -t 1 ]]; then
    qrencode -t ANSIUTF8 "$PAIR_URI"
  else
    echo "Показать код: $APP_DIR/show-pairing-qr.sh"
  fi
fi
echo "Текущая задача: $THREAD_ID"
echo
exec python3 "$APP_DIR/bridge/server.py" \
  --thread "$THREAD_ID" --bind "0.0.0.0" --port 8765 \
  --pin "$PAIR_PIN" \
  --cert "$CERT_FILE" --key "$KEY_FILE" --inbox "$APP_DIR/inbox" \
  --outbox "$APP_DIR/outbox" --state "$STATE_FILE"
