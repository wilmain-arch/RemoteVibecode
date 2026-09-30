#!/usr/bin/env python3
"""Temporarily serve only the debug APK on the PC's LAN address."""

from __future__ import annotations

import ipaddress
import html
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APK = Path(__file__).resolve().parent / "android/app/build/outputs/apk/debug/app-debug.apk"
routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "default"], text=True))
interface = next((route.get("dev") for route in routes if route.get("dev") != "tailscale0"), None)
if not interface:
    raise SystemExit("LAN-интерфейс ПК не найден")
addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr", "show", "dev", interface], text=True))
BIND = next((address.get("local") for item in addresses
             for address in item.get("addr_info", []) if address.get("scope") == "global"), None)
if not BIND:
    raise SystemExit("LAN-адрес ПК не найден")
PORT = int(os.environ.get("CODEX_PHONE_APK_PORT", "8766"))
TOKEN = secrets.token_urlsafe(20)
PATH = f"/{TOKEN}/Codex-Phone-Companion.apk"
PAGE = f"/{TOKEN}/"
PAIRING_URI = Path.home() / ".config/codex-phone-companion/pairing-uri"
STATE_FILE = Path.home() / ".config/codex-phone-companion/state.json"
EXPIRES = time.monotonic() + 30 * 60


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("download:", fmt % args, flush=True)

    def do_GET(self):
        try:
            source_ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            self.send_error(403)
            return
        allowed = source_ip.is_private or source_ip in ipaddress.ip_network("100.64.0.0/10")
        if not allowed or time.monotonic() >= EXPIRES:
            self.send_error(410 if time.monotonic() >= EXPIRES else 403)
            return
        if self.path == PAGE:
            paired = False
            try:
                paired = bool(json.loads(STATE_FILE.read_text()).get("paired"))
            except (OSError, ValueError):
                pass
            pair_link = ""
            if not paired and PAIRING_URI.is_file():
                pair_link = '<p><a href="' + html.escape(PAIRING_URI.read_text().strip(), quote=True) + '">2. Открыть приложение и подключить телефон</a></p>'
            body = (
                '<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>RemoteVibecode</title><style>body{font:18px system-ui;max-width:38rem;margin:3rem auto;padding:1rem;line-height:1.5}a{display:block;padding:1rem;background:#176b5a;color:white;border-radius:12px;text-decoration:none}</style>'
                '<h1>RemoteVibecode</h1><p><a href="' + PATH + '">1. Скачать APK</a></p>'
                + pair_link + '<p>После первой привязки приложение открывает переписку без входа.</p></html>'
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != PATH:
            self.send_error(404)
            return
        size = APK.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Disposition", 'attachment; filename="Codex-Phone-Companion.apk"')
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with APK.open("rb") as source:
            while chunk := source.read(128 * 1024):
                self.wfile.write(chunk)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def stop(server):
    server.shutdown()


if __name__ == "__main__":
    if not APK.is_file():
        raise SystemExit(f"APK не найден: {APK}")
    server = Server((BIND, PORT), Handler)
    timer = threading.Timer(30 * 60, stop, args=(server,))
    timer.daemon = True
    timer.start()
    print(f"Временная страница установки через LAN или маршрутизатор подсети (30 минут): http://{BIND}:{PORT}{PAGE}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        timer.cancel()
