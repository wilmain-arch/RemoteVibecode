#!/usr/bin/env bash
set -euo pipefail
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/codex-phone-companion"
STATE_FILE="$CONFIG_DIR/state.json"
systemctl --user stop codex-phone-companion.service
python3 - "$STATE_FILE" <<'PY'
import json, os, secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {}
data.update(token=secrets.token_urlsafe(32), paired=False,
            pending={}, queued_sends={}, cancelled_sends={}, sent_messages={})
temporary = path.with_name(path.name + ".reset")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as out:
    json.dump(data, out, ensure_ascii=False)
    out.flush()
    os.fsync(out.fileno())
os.replace(temporary, path)
PY
systemctl --user start codex-phone-companion.service
echo "Привязка отозвана. Открой страницу установки или ./show-pairing-qr.sh."
