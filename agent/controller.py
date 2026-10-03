"""UI-independent agent operations, delivered to the Qt UI by queued signals."""

from __future__ import annotations
import json, os, ssl, threading, time, webbrowser
from pathlib import Path
from datetime import datetime
from urllib import request
from PySide6.QtCore import QObject, Signal, Qt
from agent import updates
from agent.windows_agent import (
    ensure_certificate,
    load_config,
    load_config_from_dict,
    pairing_image,
    run_agent,
)


class EventSink(QObject):
    received = Signal(object)

    def put(self, value):
        self.received.emit(value)


class AgentController(QObject):
    changed = Signal()
    close_requested = Signal()

    def __init__(self, config_path: Path, *, offline_preview=False):
        super().__init__()
        self.config_path = config_path
        self.offline_preview = offline_preview
        self.update_info = None
        self.update_message = ""
        self.update_busy = False
        self.update_downloading = False
        self.update_waiting = False
        self.update_error = ""
        self.update_progress = 0
        self.update_file = None
        self.update_cancel = threading.Event()
        self.update_checked = ""
        self.update_cache = config_path.parent / "updates" / "status.json"
        if not offline_preview:
            try:
                cached = json.loads(self.update_cache.read_text(encoding="utf-8"))
                self.update_info = cached.get("result")
                if self.update_info:
                    self.update_info["available"] = updates.version_tuple(
                        self.update_info["info"]["version"]
                    ) > updates.version_tuple(updates.VERSION)
                self.update_checked = cached.get("checked", "")
                self.update_message = cached.get("message", "")
                if self.update_info and not self.update_info.get("available"):
                    self.update_message = "Установлена актуальная версия"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
            result_file = config_path.parent / "updates" / "result.json"
            if result_file.is_file():
                try:
                    self.update_message = json.loads(
                        result_file.read_text(encoding="utf-8")
                    )["message"]
                    result_file.unlink()
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        self.events = EventSink(self)
        self.events.received.connect(self.consume, Qt.ConnectionType.QueuedConnection)
        self.stop = threading.Event()
        self.started = self.bridge_ready = self.relay_ready = False
        self.error = self.message = self.pairing_path = ""
        self.operation_busy = False
        self.configured = True if offline_preview else config_path.is_file()
        self.config = (
            {"relayHost": "relay.example.org", "bridgePort": 18765, "publicPort": 8765}
            if offline_preview
            else {}
        )
        if self.configured and not offline_preview:
            try:
                self.config = load_config(config_path)
            except Exception as exc:
                self.error = f"Ошибка настроек: {exc}"
                self.configured = False
        self.last_paired = self.paired()

    def draw(self):
        self.changed.emit()

    def exit_agent(self):
        self.stop.set()
        self.close_requested.emit()

    def consume(self, event):
        if event[0] == "update":
            _, kind, value = event
            if kind == "checked":
                if (self.update_info or {}).get("info", {}).get("sha256") != value[
                    "info"
                ]["sha256"]:
                    self.update_file = None
                self.update_info = value
                self.update_busy = False
                self.update_error = ""
                self.update_checked = datetime.now().strftime("%d.%m.%Y %H:%M")
                self.update_message = (
                    f"Доступна версия {value['info']['version']}"
                    if value["available"]
                    else "Установлена актуальная версия"
                )
                try:
                    self.save_update_status()
                except (OSError, ValueError, TypeError):
                    pass
            elif kind == "error":
                self.update_busy = self.update_downloading = self.update_waiting = False
                self.update_error = self.update_message = value
            elif kind == "progress":
                self.update_progress = value
                self.update_message = f"Скачано {value}%"
            elif kind == "downloaded":
                self.update_file, self.update_info = value
                self.update_downloading = False
                self.update_message = "Файл проверен. Можно установить обновление."
            elif kind == "waiting":
                self.update_message = value
            elif kind == "install":
                if self.update_cancel.is_set():
                    self.release_update_lock()
                    self.update_waiting = False
                    self.update_message = "Установка отменена."
                else:
                    try:
                        updates.prepare_install(
                            self.update_info["info"], self.update_file, self.config_path
                        )
                        self.exit_agent()
                    except Exception as exc:
                        self.release_update_lock()
                        self.update_waiting = False
                        self.update_error = f"Не удалось начать установку: {exc}"
        elif event[0] == "bridge":
            self.bridge_ready = event[1]
        elif event[0] == "relay":
            self.relay_ready = event[1]
            if len(event) > 2:
                self.error = event[2]
            elif event[1]:
                self.error = ""
        elif event[0] == "pairing":
            self.pairing_path = event[1]
        elif event[0] == "error":
            self.error = event[1]
        elif event[0] == "message":
            self.message = event[1]
        elif event[0] == "operation":
            self.operation_busy = False
            _, ok, text, path = event
            if ok:
                self.message = text
                self.error = ""
                if path:
                    self.pairing_path = path
            else:
                self.error = text
        self.changed.emit()

    def refresh_state(self):
        now = self.paired()
        if now != self.last_paired or (
            self.pairing_path and not Path(self.pairing_path).is_file()
        ):
            self.last_paired = now
            if self.pairing_path and not Path(self.pairing_path).is_file():
                self.pairing_path = ""
            self.changed.emit()

    def pairing_request(self, *, unpair=False):
        if self.offline_preview:
            self.message = "Предпросмотр: действия привязки отключены."
            self.draw()
            return
        if self.operation_busy or not self.bridge_ready:
            return
        self.operation_busy = True
        self.error = ""
        self.draw()
        # Capture the active connection, not an edited-but-not-applied settings draft.
        config = dict(self.active_config)
        previous = self.pairing_path

        def work():
            try:
                state = json.loads((self.config_path.parent / "state.json").read_text())

                def post(path):
                    req = request.Request(
                        f"https://127.0.0.1:{config['bridgePort']}/api/{path}",
                        data=b"{}",
                        method="POST",
                        headers={
                            "Authorization": "Bearer " + state["token"],
                            "Content-Type": "application/json",
                        },
                    )
                    with request.urlopen(
                        req, context=ssl._create_unverified_context(), timeout=10
                    ) as response:
                        return json.load(response)

                if unpair:
                    post("unpair")
                pin = post("pairing/rotate")["pin"]
                _, _, fingerprint = ensure_certificate(self.config_path.parent)
                path = str(
                    pairing_image(config, fingerprint, pin, self.config_path.parent)
                )
                if previous and previous != path:
                    Path(previous).unlink(missing_ok=True)
                self.events.put(
                    (
                        "operation",
                        True,
                        "Новый QR-код готов. Он действует 30 минут.",
                        path,
                    )
                )
            except Exception as exc:
                self.events.put(
                    ("operation", False, f"Не удалось изменить привязку: {exc}", None)
                )

        threading.Thread(target=work, daemon=True).start()

    @property
    def active_config(self):
        return getattr(self, "_active_config", self.config)

    def save_settings(self, values):
        if self.offline_preview:
            self.message = "Предпросмотр: настройки не записаны."
            self.draw()
            return False
        data = {key: value.strip() for key, value in values.items()}
        for key, default in [
            ("publicPort", 8765),
            ("controlPort", 8766),
            ("dataPort", 8767),
            ("bridgePort", 18765),
        ]:
            data[key] = self.config.get(key, default)
        try:
            validated = load_config_from_dict(data)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix(".tmp")
            if os.name != "nt":
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(validated, out, ensure_ascii=False, indent=2)
                temporary.chmod(0o600)
            else:
                temporary.write_text(
                    json.dumps(validated, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            os.replace(temporary, self.config_path)
            needs_restart = self.started and validated != self.active_config
            self.config = validated
            self.configured = True
            self.error = ""
            self.message = (
                "Настройки сохранены. Перезапустите агент для применения подключения."
                if needs_restart
                else "Настройки сохранены."
            )
            if not self.started:
                self.start_agent()
        except Exception as exc:
            self.error = f"Не удалось сохранить настройки: {exc}"
            self.draw()
            return False
        self.draw()
        return True

    def start_agent(self):
        if self.offline_preview:
            return
        if self.started or not self.configured:
            return
        self.started = True
        self._active_config = dict(self.config)

        def worker():
            try:
                run_agent(
                    self.config_path,
                    lambda *args: self.events.put(args),
                    False,
                    self.stop,
                )
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def paired(self) -> bool:
        if self.offline_preview:
            return False
        try:
            return bool(
                json.loads(
                    (self.config_path.parent / "state.json").read_text(encoding="utf-8")
                ).get("paired")
            )
        except (OSError, ValueError):
            return False

    def save_update_status(self, attempt=None):
        self.update_cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            old = json.loads(self.update_cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if not isinstance(old, dict):
            old = {}
        data = {
            "result": self.update_info,
            "checked": self.update_checked,
            "message": self.update_message,
            "attempt": attempt or old.get("attempt", 0),
        }
        temporary = self.update_cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.update_cache)

    def check_update(self, manual=True):
        if self.offline_preview:
            self.message = "Предпросмотр: проверка обновлений отключена."
            self.draw()
            return
        if self.update_busy or self.update_downloading or self.update_waiting:
            return
        try:
            last = json.loads(self.update_cache.read_text(encoding="utf-8")).get(
                "attempt", 0
            )
        except (OSError, ValueError, TypeError, AttributeError):
            last = 0
        if not manual and time.time() - last < updates.DAY:
            return
        self.update_busy = True
        self.update_message = "Проверяю обновления…"
        try:
            self.save_update_status(attempt=time.time())
        except OSError:
            pass
        self.draw()

        def work():
            try:
                self.events.put(("update", "checked", updates.check()))
            except Exception as exc:
                self.events.put(("update", "error", f"Не удалось проверить: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def download_update(self):
        if self.offline_preview:
            self.message = "Предпросмотр: скачивание обновлений отключено."
            self.draw()
            return
        if self.update_busy or self.update_downloading or self.update_waiting:
            return
        self.update_downloading = True
        self.update_cancel.clear()
        self.update_message = "Проверяю релиз и скачиваю файл…"
        self.draw()

        def work():
            try:
                # Never install from unsigned mutable local UI cache.
                result = updates.check()
                if not result["available"]:
                    raise RuntimeError("Установлена актуальная версия")
                previous = [-1]

                def progress(percent):
                    if previous[0] != percent:
                        previous[0] = percent
                        self.events.put(("update", "progress", percent))

                path = updates.download(
                    result["info"],
                    self.config_path.parent / "updates",
                    progress,
                    self.update_cancel,
                )
                self.events.put(("update", "downloaded", (path, result)))
            except Exception as exc:
                self.events.put(("update", "error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def release_update_lock(self):
        if not self.started:
            return

        def release():
            try:
                state = json.loads(
                    (self.config_path.parent / "state.json").read_text(encoding="utf-8")
                )
                req = request.Request(
                    f"https://127.0.0.1:{self.active_config['bridgePort']}/api/update/prepare",
                    data=b'{"release":true}',
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + state["token"],
                        "Content-Type": "application/json",
                    },
                )
                with request.urlopen(
                    req, context=ssl._create_unverified_context(), timeout=5
                ):
                    pass
            except Exception:
                pass  # Lease expires automatically in 120 seconds.

        threading.Thread(target=release, daemon=True).start()

    def install_update(self):
        if self.offline_preview:
            self.message = "Предпросмотр: установка обновлений отключена."
            self.draw()
            return
        if (
            self.update_busy
            or self.update_downloading
            or self.update_waiting
            or self.update_file is None
        ):
            return
        self.update_waiting = True
        self.update_cancel.clear()
        self.update_message = "Проверяю задачи перед установкой…"
        self.draw()

        def work():
            try:
                while not self.update_cancel.is_set():
                    if not self.started:
                        self.events.put(("update", "install", None))
                        return
                    state = json.loads(
                        (self.config_path.parent / "state.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    req = request.Request(
                        f"https://127.0.0.1:{self.active_config['bridgePort']}/api/update/prepare",
                        data=b'{"arm":true}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer " + state["token"],
                            "Content-Type": "application/json",
                        },
                    )
                    with request.urlopen(
                        req, context=ssl._create_unverified_context(), timeout=30
                    ) as response:
                        status = json.load(response)
                    if status.get("ready") and status.get("armed"):
                        self.events.put(("update", "install", None))
                        return
                    self.events.put(
                        (
                            "update",
                            "waiting",
                            f"Жду завершения задач: {status.get('active', 0)} · В очереди: {status.get('queued', 0)}",
                        )
                    )
                    self.update_cancel.wait(5)
                self.events.put(
                    ("update", "error", "Установка отменена. Скачанный файл сохранён.")
                )
            except Exception as exc:
                self.events.put(
                    (
                        "update",
                        "error",
                        f"Установка отложена: не удалось подтвердить завершение задач ({exc})",
                    )
                )

        threading.Thread(target=work, daemon=True).start()
