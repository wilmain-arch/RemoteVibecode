#!/usr/bin/env python3
"""PC bridge and outbound relay client for RemoteVibecode."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
from urllib.parse import urlencode

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import qrcode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import server as bridge_server


APP_NAME = "RemoteVibecode"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
                  or Path.home() / ".config") / APP_NAME
CONFIG_FILE = CONFIG_DIR / "agent.json"
HEX_64 = re.compile(r"[0-9a-fA-F]{64}")


def normalize_pin(value: str) -> str:
    result = "".join(char for char in value if char in "0123456789abcdefABCDEF").upper()
    if not HEX_64.fullmatch(result):
        raise ValueError("Отпечаток должен содержать 64 шестнадцатеричных символа")
    return result


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    host = str(data.get("relayHost", "")).strip()
    secret = str(data.get("relaySecret", "")).strip()
    if not host or "/" in host or "://" in host or ":" in host:
        raise ValueError("relayHost должен быть доменом или IPv4-адресом без порта")
    if not HEX_64.fullmatch(secret):
        raise ValueError("relaySecret должен быть 64-символьным ключом сервера")
    data["relayFingerprint"] = normalize_pin(str(data.get("relayFingerprint", "")))
    for name, default in (("publicPort", 8765), ("controlPort", 8766),
                          ("dataPort", 8767), ("bridgePort", 18765)):
        port = int(data.get(name, default))
        if not 1 <= port <= 65535:
            raise ValueError(f"Некорректный порт {name}")
        data[name] = port
    return data


def setup(path: Path) -> None:
    print("Настройка RemoteVibecode на этом ПК")
    host = input("Адрес вашего сервера (домен или IPv4, без порта): ").strip()
    fingerprint = normalize_pin(input("SHA-256 отпечаток сертификата ретранслятора: "))
    secret = getpass.getpass("Секрет сервера из /etc/remotevibecode/relay-secret: ").strip()
    data = {"relayHost": host, "relayFingerprint": fingerprint,
            "relaySecret": secret, "publicPort": 8765, "controlPort": 8766,
            "dataPort": 8767, "bridgePort": 18765,
            "codexExecutable": shutil.which("codex") or ""}
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file contains the relay secret; protect it on Linux as well as Windows.
    validated = load_config_from_dict(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(validated, stream, indent=2)
    if sys.platform != "win32":
        path.chmod(0o600)
    print(f"Настройки сохранены: {path}")


def load_config_from_dict(data: dict) -> dict:
    # Use the same validation for interactive setup and future launches.
    host = str(data.get("relayHost", "")).strip()
    secret = str(data.get("relaySecret", "")).strip()
    if not host or "/" in host or "://" in host or ":" in host or not HEX_64.fullmatch(secret):
        raise ValueError("Некорректный адрес или секрет сервера")
    data["relayFingerprint"] = normalize_pin(str(data.get("relayFingerprint", "")))
    return data


def ensure_certificate(directory: Path) -> tuple[Path, Path, str]:
    cert_path = directory / "bridge.crt"
    key_path = directory / "bridge.key"
    if not cert_path.is_file() or not key_path.is_file():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "RemoteVibecode PC")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=825))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_bytes = key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key_bytes)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if sys.platform != "win32":
        key_path.chmod(0o600)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint = cert.fingerprint(hashes.SHA256()).hex().upper()
    return cert_path, key_path, fingerprint


def pinned_connection(host: str, port: int, fingerprint: str) -> ssl.SSLSocket:
    raw = socket.create_connection((host, port), timeout=10)
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # The exact SHA-256 certificate pin is checked below.
        wrapped = context.wrap_socket(raw, server_hostname=host)
        actual = hashlib.sha256(wrapped.getpeercert(binary_form=True)).hexdigest().upper()
        if not hmac.compare_digest(actual, fingerprint):
            wrapped.close()
            raise ssl.SSLError("Отпечаток сервера не совпадает")
        return wrapped
    except Exception:
        raw.close()
        raise


def read_json(sock: socket.socket) -> dict:
    line = bytearray()
    while len(line) < 4096:
        char = sock.recv(1)
        if not char:
            raise ConnectionError("Сервер закрыл соединение")
        if char == b"\n":
            result = json.loads(line)
            if isinstance(result, dict):
                return result
            break
        line.extend(char)
    raise ConnectionError("Некорректный ответ сервера")


def write_json(sock: socket.socket, value: dict) -> None:
    sock.sendall(json.dumps(value, separators=(",", ":")).encode() + b"\n")


def proof(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def tunnel(config: dict, connection_id: str) -> None:
    local = remote = None
    try:
        local = socket.create_connection(("127.0.0.1", config["bridgePort"]), timeout=10)
        remote = pinned_connection(config["relayHost"], config["dataPort"], config["relayFingerprint"])
        write_json(remote, {"id": connection_id,
                            "proof": proof(config["relaySecret"], "data:" + connection_id)})
        if read_json(remote).get("ready") is not True:
            raise ConnectionError("Сервер отклонил канал данных")
        local.settimeout(None)
        remote.settimeout(None)

        def copy(source: socket.socket, destination: socket.socket) -> None:
            try:
                while chunk := source.recv(65536):
                    destination.sendall(chunk)
            except (OSError, ssl.SSLError):
                pass
            finally:
                source.close()
                destination.close()

        uplink = threading.Thread(target=copy, args=(local, remote), daemon=True)
        uplink.start()
        copy(remote, local)
        uplink.join(timeout=2)
    except (OSError, ValueError, ConnectionError) as error:
        print(f"Канал телефона: {error}", flush=True)
    finally:
        if local is not None:
            local.close()
        if remote is not None:
            remote.close()


def relay_loop(config: dict, status=None, stop=None) -> None:
    while stop is None or not stop.is_set():
        control = None
        stop_ping = threading.Event()
        try:
            control = pinned_connection(config["relayHost"], config["controlPort"],
                                        config["relayFingerprint"])
            challenge = str(read_json(control).get("challenge", ""))
            if not HEX_64.fullmatch(challenge):
                raise ConnectionError("Некорректный запрос сервера")
            write_json(control, {"proof": proof(config["relaySecret"], "agent:" + challenge)})
            if read_json(control).get("ready") is not True:
                raise ConnectionError("Сервер отклонил агента")
            print("Соединение с сервером установлено", flush=True)
            if status:
                status("relay", True)

            def ping() -> None:
                while not stop_ping.wait(20):
                    try:
                        write_json(control, {"type": "ping"})
                    except OSError:
                        return

            threading.Thread(target=ping, daemon=True).start()
            control.settimeout(None)
            while True:
                message = read_json(control)
                connection_id = message.get("connect")
                if isinstance(connection_id, str) and re.fullmatch(r"[0-9a-f]{32}", connection_id):
                    threading.Thread(target=tunnel, args=(config, connection_id), daemon=True).start()
        except (OSError, ValueError, ConnectionError, json.JSONDecodeError) as error:
            print(f"Нет связи с сервером: {error}; повтор через 5 секунд", flush=True)
            if status:
                status("relay", False, str(error))
        finally:
            if status:
                status("relay", False)
            stop_ping.set()
            if control is not None:
                control.close()
        if stop is not None:
            stop.wait(5)
        else:
            time.sleep(5)


def wait_for_bridge(port: int, thread: threading.Thread) -> None:
    for _ in range(50):
        if not thread.is_alive():
            raise RuntimeError("Локальный мост не запустился")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Локальный мост не открыл порт")


def pairing_image(config: dict, fingerprint: str, pin: str, directory: Path) -> Path:
    address = f'https://{config["relayHost"]}:{config["publicPort"]}'
    uri = "codexphone://pair?" + urlencode({"host": address, "pin": pin, "fingerprint": fingerprint})
    image_path = directory / f"pairing-{pin}.png"
    qrcode.make(uri).save(image_path)
    return image_path


def show_pairing(config: dict, fingerprint: str, pin: str, directory: Path, open_image=True) -> Path | None:
    state_path = directory / "state.json"
    try:
        if json.loads(state_path.read_text(encoding="utf-8")).get("paired"):
            print("Телефон уже привязан", flush=True)
            return None
    except (OSError, ValueError):
        pass
    image_path = pairing_image(config, fingerprint, pin, directory)
    print(f"QR-код привязки: {image_path} (действует 30 минут)", flush=True)
    if open_image and sys.platform == "win32":
        try:
            os.startfile(image_path)
        except OSError:
            print("Откройте QR-файл вручную", flush=True)

    def remove_when_used() -> None:
        for _ in range(360):
            time.sleep(5)
            try:
                if json.loads(state_path.read_text(encoding="utf-8")).get("paired"):
                    break
            except (OSError, ValueError):
                pass
        image_path.unlink(missing_ok=True)

    threading.Thread(target=remove_when_used, daemon=True).start()
    return image_path


def run_agent(config_path: Path, status=None, open_pairing=True, stop=None) -> None:
    config = load_config(config_path)
    directory = config_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    cert, key, bridge_fingerprint = ensure_certificate(directory)
    pin = f"{secrets.randbelow(100_000_000):08d}"
    desktop_codex = Path("/usr/lib/chatgpt/resources/codex")
    codex_executable = (config.get("codexExecutable") or shutil.which("codex")
                        or (str(desktop_codex) if desktop_codex.is_file() else None))
    if not codex_executable:
        raise RuntimeError("Codex CLI не найден. Установите Codex на ПК и укажите путь в настройках")
    os.environ["CODEX_EXECUTABLE"] = codex_executable
    bridge_args = ["--thread", "draft-default", "--bind", "127.0.0.1",
                   "--port", str(config["bridgePort"]), "--pin", pin,
                   "--cert", str(cert), "--key", str(key),
                   "--inbox", str(directory / "inbox"),
                   "--outbox", str(directory / "outbox"),
                   "--state", str(directory / "state.json")]
    bridge_thread = threading.Thread(target=bridge_server.main, args=(bridge_args,), daemon=True)
    bridge_thread.start()
    wait_for_bridge(config["bridgePort"], bridge_thread)
    if status:
        status("bridge", True)
    pairing_path = show_pairing(config, bridge_fingerprint, pin, directory, open_pairing)
    if status:
        status("pairing", str(pairing_path) if pairing_path else "")
    relay_loop(config, status, stop)


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise RuntimeError("Автозапуск доступен в Windows EXE")
    import winreg
    registry = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                             winreg.KEY_SET_VALUE)
    with registry:
        if enabled:
            winreg.SetValueEx(registry, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(registry, APP_NAME)
            except FileNotFoundError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    parser.add_argument("--cli", action="store_true", help="Запустить без графического интерфейса")
    args = parser.parse_args()
    if not args.cli and not (args.setup or args.install_autostart or args.remove_autostart):
        from agent.gui import launch
        launch(args.config)
        return
    if args.setup or not args.config.is_file():
        setup(args.config)
    if args.install_autostart or args.remove_autostart:
        set_autostart(args.install_autostart)
        print("Автозапуск " + ("включён" if args.install_autostart else "выключен"))
        return
    run_agent(args.config)


if __name__ == "__main__":
    main()
