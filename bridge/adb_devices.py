"""Managed, narrowly scoped wireless ADB connections for the phone bridge."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path


class AdbDevices:
    def __init__(self, path: Path, relay: str = "") -> None:
        self.path = path
        self.relay = relay
        self.lock = threading.RLock()
        self.tunnels: dict[str, subprocess.Popen] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.devices = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.devices = {}
        self.events_path = path.with_name("adb-events.jsonl")
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def _event(self, device: dict, status: str, detail: str = "") -> None:
        device["status"] = status
        device["lastChangeAt"] = int(time.time())
        device["lastError"] = detail[:300] if status != "connected" else ""
        if status == "connected":
            device["lastSeenAt"] = device["lastChangeAt"]
        line = {"time": device["lastChangeAt"], "id": device["id"], "name": device["name"],
                "status": status, "detail": detail[:300]}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(line, ensure_ascii=False) + "\n")
        os.chmod(self.events_path, 0o600)
        self._save()

    def _monitor(self) -> None:
        while not self.stop.wait(15):
            with self.lock:
                try:
                    active = self._run("devices", timeout=8)
                except RuntimeError:
                    continue
                for device in self.devices.values():
                    endpoint = device.get("endpoint", "")
                    if not endpoint:
                        continue
                    online = f"{endpoint}\tdevice" in active
                    if online:
                        if device.get("status") != "connected":
                            self._event(device, "connected", "Соединение восстановлено")
                        else:
                            device["lastSeenAt"] = int(time.time())
                        continue
                    if device.get("status") == "connected":
                        self._event(device, "offline", "ADB перестал отвечать")
                    last_attempt = device.get("lastAttemptAt", 0)
                    if time.time() - last_attempt < 30:
                        continue
                    device["lastAttemptAt"] = time.time()
                    try:
                        self.connect(device["id"])
                    except RuntimeError as exc:
                        detail = str(exc)[:300]
                        if detail != device.get("lastError"):
                            self._event(device, "offline", detail)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.devices, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @staticmethod
    def _port(value) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise ValueError("Укажите порт от 1 до 65535") from None
        if not 1 <= port <= 65535:
            raise ValueError("Укажите порт от 1 до 65535")
        return port

    @staticmethod
    def _address(value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("Укажите числовой IP-адрес устройства") from None
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError("Этот адрес устройства недопустим")
        return str(address)

    def _relay_parts(self) -> tuple[str, str, int]:
        match = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_.-]*)@([a-zA-Z0-9.-]+):(\d{1,5})", self.relay)
        if not match:
            raise ValueError("SSH-ретранслятор не настроен на ПК")
        return match[1], match[2], self._port(match[3])

    def _endpoint(self, device: dict) -> str:
        if device["transport"] == "direct":
            return f'{device["address"]}:{device["connectPort"]}'
        tunnel = self.tunnels.get(device["id"])
        if tunnel is not None and tunnel.poll() is None:
            return f'127.0.0.1:{device["localPort"]}'
        user, host, ssh_port = self._relay_parts()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            local_port = probe.getsockname()[1]
        target = f'{device["address"]}:{device["connectPort"]}'
        command = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                   "-o", "ConnectTimeout=5", "-p", str(ssh_port), "-L",
                   f"127.0.0.1:{local_port}:{target}", f"{user}@{host}"]
        tunnel = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(30):
            if tunnel.poll() is not None:
                detail = tunnel.stderr.read(400).decode(errors="replace") if tunnel.stderr else ""
                raise RuntimeError("SSH-туннель не запустился: " + detail.strip())
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            tunnel.terminate()
            raise RuntimeError("SSH-туннель не открыл локальный порт")
        self.tunnels[device["id"]] = tunnel
        device["localPort"] = local_port
        return f"127.0.0.1:{local_port}"

    def _run(self, *args: str, timeout: int = 12) -> str:
        try:
            result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("ADB не установлен на ПК") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("ADB не ответил вовремя") from None
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode:
            raise RuntimeError(output[:400] or "Ошибка ADB")
        return output

    def list(self) -> dict:
        with self.lock:
            active = self._run("devices")
            for device in self.devices.values():
                endpoint = device.get("endpoint", "")
                state = "connected" if endpoint and f"{endpoint}\tdevice" in active else "offline"
                if device.get("status") != state and endpoint:
                    self._event(device, state, "ADB перестал отвечать" if state == "offline" else "Соединение восстановлено")
                else:
                    device["status"] = state
            return {"devices": [self._public(item) for item in self.devices.values()],
                    "relayAvailable": bool(self.relay), "events": self.recent_events()}

    def recent_events(self) -> list[dict]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()[-20:]
            return [json.loads(line) for line in reversed(lines)]
        except (OSError, ValueError):
            return []

    @staticmethod
    def _public(item: dict) -> dict:
        return {key: item.get(key) for key in
                ("id", "name", "address", "transport", "connectPort", "status", "model",
                 "lastSeenAt", "lastChangeAt", "lastError")}

    def add(self, data: dict) -> dict:
        name = str(data.get("name", "")).strip()[:60]
        if not name:
            raise ValueError("Укажите название устройства")
        transport = data.get("transport")
        if transport not in ("direct", "ssh_relay"):
            raise ValueError("Выберите способ подключения")
        if transport == "ssh_relay":
            self._relay_parts()
        device = {"id": secrets.token_hex(8), "name": name,
                  "address": self._address(str(data.get("address", ""))),
                  "transport": transport, "connectPort": self._port(data.get("connectPort")),
                  "status": "offline", "model": ""}
        with self.lock:
            self.devices[device["id"]] = device
            self._save()
        return self._public(device)

    def update_port(self, device_id: str, port) -> dict:
        with self.lock:
            device = self._get(device_id)
            self._close_tunnel(device_id)
            device["connectPort"] = self._port(port)
            device.pop("endpoint", None)
            device["status"] = "offline"
            self._save()
            return self._public(device)

    def _get(self, device_id: str) -> dict:
        if device_id not in self.devices:
            raise ValueError("Устройство не найдено")
        return self.devices[device_id]

    def _close_tunnel(self, device_id: str) -> None:
        tunnel = self.tunnels.pop(device_id, None)
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                tunnel.kill()

    def pair(self, device_id: str, pairing_port, code: str) -> dict:
        port = self._port(pairing_port)
        if not re.fullmatch(r"\d{6}", str(code)):
            raise ValueError("Код сопряжения должен состоять из шести цифр")
        with self.lock:
            device = self._get(device_id)
            # Pairing uses a separate, short-lived port. Keep its tunnel only for this call.
            if device["transport"] == "direct":
                endpoint = f'{device["address"]}:{port}'
                return {"message": self._run("pair", endpoint, str(code), timeout=15)}
            user, host, ssh_port = self._relay_parts()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                local_port = probe.getsockname()[1]
            command = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                       "-o", "ConnectTimeout=5", "-p", str(ssh_port), "-L",
                       f'127.0.0.1:{local_port}:{device["address"]}:{port}', f"{user}@{host}"]
            tunnel = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(30):
                    if tunnel.poll() is not None:
                        raise RuntimeError("SSH-туннель для сопряжения не запустился")
                    try:
                        with socket.create_connection(("127.0.0.1", local_port), timeout=.2):
                            break
                    except OSError:
                        time.sleep(.1)
                return {"message": self._run("pair", f"127.0.0.1:{local_port}", str(code), timeout=15)}
            finally:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    tunnel.kill()

    def connect(self, device_id: str) -> dict:
        with self.lock:
            device = self._get(device_id)
            try:
                endpoint = self._endpoint(device)
                output = self._run("connect", endpoint, timeout=15)
                state = self._run("-s", endpoint, "get-state", timeout=8)
                if state.strip() != "device":
                    raise RuntimeError(output or "Телефон не авторизован")
                device["endpoint"] = endpoint
                device["model"] = self._run("-s", endpoint, "shell", "getprop", "ro.product.model", timeout=8).strip()
                self._event(device, "connected", "ADB подключён")
                return self._public(device)
            except RuntimeError as exc:
                self._event(device, "offline", str(exc))
                raise

    def delete(self, device_id: str) -> dict:
        with self.lock:
            device = self._get(device_id)
            endpoint = device.get("endpoint")
            if endpoint:
                try:
                    self._run("disconnect", endpoint, timeout=5)
                except RuntimeError:
                    pass
            self._close_tunnel(device_id)
            del self.devices[device_id]
            self._save()
            return {"deleted": True}
