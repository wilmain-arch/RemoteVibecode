"""Desktop control panel for the Windows bridge agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import ssl
import sys
import threading
import time
import webbrowser
from datetime import datetime
import tkinter as tk
from urllib import request

from PIL import Image, ImageTk
import pystray

from agent.dashboard import WIDTH as DASH_WIDTH, HEIGHT as DASH_HEIGHT, render_dashboard, render_page, render_updates
from agent import updates
from agent.windows_agent import ensure_certificate, load_config, load_config_from_dict, pairing_image, run_agent


BG = "#080e1a"
STROKE = "#25314a"
WHITE = "#f5f7ff"
BLUE = "#527cff"
FONT = "Segoe UI"


class AgentWindow:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.update_info = None
        self.update_message = ""
        self.update_busy = False
        self.update_downloading = False
        self.update_waiting = False
        self.update_progress = 0
        self.update_file = None
        self.update_cancel = threading.Event()
        self.update_checked = ""
        self.update_cache = config_path.parent / "updates" / "status.json"
        try:
            cached = json.loads(self.update_cache.read_text(encoding="utf-8"))
            self.update_info = cached.get("result")
            if self.update_info:
                self.update_info['available'] = updates.version_tuple(self.update_info['info']['version']) > updates.version_tuple(updates.VERSION)
            self.update_checked = cached.get("checked", "")
            self.update_message = cached.get("message", "")
            if self.update_info and not self.update_info.get('available'):
                self.update_message = "Установлена актуальная версия"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        result_file = config_path.parent / "updates" / "result.json"
        if result_file.is_file():
            try:
                self.update_message = json.loads(result_file.read_text(encoding="utf-8"))["message"]
                result_file.unlink()
            except (OSError, ValueError, KeyError, TypeError): pass
        self.root = tk.Tk()
        self.root.title("RemoteVibecode")
        self.root.geometry("1500x845")
        self.root.minsize(1040, 620)
        self.root.configure(bg=BG)
        icon_path = Path(__file__).with_name("remotevibecode.ico")
        with Image.open(icon_path) as source:
            self.window_icon = ImageTk.PhotoImage(source.convert("RGBA").resize((64, 64)))
        self.root.iconphoto(True, self.window_icon)
        if sys.platform == "win32":
            self.root.iconbitmap(default=str(icon_path))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.tray_ready = False
        self.tray_icon = None
        self.stop = threading.Event()
        self.started = False
        self.bridge_ready = False
        self.relay_ready = False
        self.error = ""
        self.pairing_path = ""
        self.qr_image = None
        self.brand_image = None
        self.page = "Обзор"
        self.form: dict[str, tk.Entry] = {}
        self.form_draft: dict[str, str] = {}
        self.message = ""
        self.configured = self.config_path.is_file()
        self.config = {}
        if self.configured:
            try:
                self.config = load_config(self.config_path)
            except Exception as exc:
                self.error = f"Ошибка настроек: {exc}"
                self.configured = False
        self.last_paired = self.paired()
        self.dashboard_image = None
        self.draw()
        self.start_tray()
        self.root.after(250, self.poll)
        if self.configured:
            self.start_agent()
        self.root.after(3000, lambda: self.check_update(manual=False))
        self.root.after(3_600_000, self.auto_update_check)

    def close(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            # Keep the agent reachable while the tray is starting or unavailable.
            self.root.iconify()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def exit_agent(self):
        self.stop.set()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()

    def start_tray(self):
        icon_path = Path(__file__).with_name("remotevibecode.ico")
        with Image.open(icon_path) as source:
            icon_image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        self.tray_icon = pystray.Icon(
            "RemoteVibecode", icon_image, "RemoteVibecode",
            menu=pystray.Menu(
                pystray.MenuItem("Открыть", lambda icon, item: self.events.put(("tray_open",)), default=True),
                pystray.MenuItem("Выйти", lambda icon, item: self.events.put(("tray_exit",))),
            ),
        )

        def run_tray():
            try:
                def ready(icon):
                    icon.visible = True
                    self.events.put(("tray_ready",))

                self.tray_icon.run(setup=ready)
            except Exception as exc:
                self.events.put(("tray_error", str(exc)))

        threading.Thread(target=run_tray, name="RemoteVibecode tray", daemon=True).start()

    def start_agent(self):
        if self.started:
            return
        self.started = True

        def worker():
            try:
                run_agent(self.config_path, lambda *args: self.events.put(args), False, self.stop)
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "update":
                _, kind, value = event
                if kind == "checked":
                    self.update_info = value
                    self.update_busy = False
                    self.update_checked = datetime.now().strftime("%d.%m.%Y %H:%M")
                    self.update_message = (f"Доступна версия {value['info']['version']}" if value['available'] else "Установлена актуальная версия")
                    try: self.save_update_status()
                    except (OSError, ValueError, TypeError, AttributeError) as exc:
                        print(f"Не удалось сохранить кэш обновлений: {exc}", flush=True)
                elif kind == "error":
                    self.update_busy = self.update_downloading = self.update_waiting = False
                    self.update_message = value
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
                        changed = True
                        continue
                    try:
                        updates.prepare_install(self.update_info['info'], self.update_file, self.config_path)
                        self.exit_agent()
                        return
                    except Exception as exc:
                        self.release_update_lock()
                        self.update_waiting = False
                        self.update_message = f"Не удалось начать установку: {exc}"
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
            elif event[0] == "tray_ready":
                self.tray_ready = True
                if self.root.state() == "iconic":
                    self.root.withdraw()
            elif event[0] == "tray_open":
                self.show_window()
            elif event[0] == "tray_exit":
                self.exit_agent()
                return
            elif event[0] == "tray_error":
                self.tray_ready = False
                if self.root.state() == "withdrawn":
                    self.show_window()
                print(f"Значок в трее недоступен: {event[1]}", flush=True)
            changed = True
        if changed:
            self.draw()
        now_paired = self.paired()
        if now_paired != self.last_paired:
            self.last_paired = now_paired
            self.draw()
        elif self.pairing_path and not Path(self.pairing_path).is_file():
            self.pairing_path = ""
            self.draw()
        self.root.after(500, self.poll)

    def paired(self) -> bool:
        try:
            return bool(json.loads((self.config_path.parent / "state.json").read_text(encoding="utf-8")).get("paired"))
        except (OSError, ValueError):
            return False

    def draw(self):
        if self.page == "Настройки" and self.form:
            self.form_draft = {key: field.get() for key, field in self.form.items()}
        for child in self.root.winfo_children():
            child.destroy()
        self.form = {}
        self.draw_dashboard()

    def draw_dashboard(self):
        canvas = tk.Canvas(self.root, bg=BG, bd=0, highlightthickness=0, cursor="arrow")
        canvas.pack(fill="both", expand=True)
        address = (f"{self.config['relayHost']}:{self.config['publicPort']}"
                   if self.configured else "")
        if self.page == "Обновления":
            info = self.update_info or {}
            base = render_updates(version=updates.VERSION, message=self.update_message,
                available=info.get('info', {}).get('version', '') if info.get('available') else '',
                downloading=self.update_downloading, progress=self.update_progress,
                ready=self.update_file is not None, waiting=self.update_waiting,
                checked=self.update_checked, notes=info.get('manifest', {}).get('notes', ''))
        elif self.page == "Обзор":
            base = render_dashboard(
                icon_path=Path(__file__).with_name("remotevibecode.ico"),
                qr_path=Path(self.pairing_path) if self.pairing_path else None,
                configured=self.configured, bridge_ready=self.bridge_ready,
                relay_ready=self.relay_ready, paired=self.paired(),
                address=address, error=self.error, status=self.message,
            )
        else:
            base = render_page(page=self.page, configured=self.configured,
                               paired=self.paired(), relay_ready=self.relay_ready,
                               address=address, error=self.error, status=self.message)
        if self.update_info and self.update_info.get('available'):
            from PIL import ImageDraw
            ImageDraw.Draw(base).ellipse((283, 494, 293, 504), fill='#14dca4')
        if self.page == "Настройки":
            fields = ("relayHost", "relayFingerprint", "relaySecret", "codexExecutable")
            for key in fields:
                field = tk.Entry(canvas, bg="#0c1525", fg=WHITE, insertbackground=WHITE,
                                 bd=0, relief="flat", font=(FONT, 16),
                                 highlightthickness=1, highlightbackground=STROKE,
                                 highlightcolor=BLUE,
                                 show="•" if key == "relaySecret" else "")
                field.insert(0, self.form_draft.get(key, str(self.config.get(key, ""))))
                self.form[key] = field

        def fit(event=None):
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 1)
            scale = min(width / DASH_WIDTH, height / DASH_HEIGHT)
            target = (max(1, int(DASH_WIDTH * scale)), max(1, int(DASH_HEIGHT * scale)))
            picture = base.resize(target, Image.Resampling.LANCZOS)
            self.dashboard_image = ImageTk.PhotoImage(picture)
            canvas.delete("all")
            canvas.create_image((width - target[0]) // 2, (height - target[1]) // 2,
                                image=self.dashboard_image, anchor="nw")
            x0, y0 = (width - target[0]) // 2, (height - target[1]) // 2
            canvas._layout = (x0, y0, scale)
            if self.page == "Настройки":
                for index, key in enumerate(("relayHost", "relayFingerprint", "relaySecret", "codexExecutable")):
                    y = 308 + index * 114
                    field = self.form[key]
                    field.configure(font=(FONT, max(9, int(16 * scale))))
                    canvas.create_window(x0 + 394 * scale, y0 + (y + 39) * scale,
                                         anchor="nw", window=field,
                                         width=int(1200 * scale), height=int(38 * scale))

        def click(event):
            x0, y0, scale = getattr(canvas, "_layout", (0, 0, 1))
            x, y = (event.x - x0) / scale, (event.y - y0) / scale
            if 32 <= x <= 308 and 183 <= y <= 540:
                index = int((y - 183) // 72)
                pages = ("Обзор", "Устройства", "Подключение", "Настройки", "Обновления")
                if 0 <= index < len(pages):
                    self.switch(pages[index])
            elif self.page == "Обзор" and 650 <= x <= 1022 and 419 <= y <= 477:
                if self.bridge_ready:
                    self.refresh_pairing()
                elif not self.configured:
                    self.switch("Настройки")
            elif self.page == "Обзор" and 1384 <= x <= 1619 and 564 <= y <= 607:
                self.switch("Устройства")
            elif self.page == "Обзор" and 1387 <= x <= 1593 and 650 <= y <= 709:
                self.switch("Устройства")
            elif self.page == "Обзор" and 1427 <= x <= 1597 and 827 <= y <= 879 and self.configured:
                address = f"https://{self.config['relayHost']}:{self.config['publicPort']}"
                self.copy_address(address)
                self.draw()
            elif self.page == "Устройства" and 1368 <= x <= 1591 and 320 <= y <= 388:
                self.unpair() if self.paired() else self.switch("Обзор")
            elif self.page == "Подключение" and 378 <= x <= 606 and 415 <= y <= 478 and self.configured:
                self.copy_address(f"https://{self.config['relayHost']}:{self.config['publicPort']}")
                self.draw()
            elif self.page == "Обновления" and 395 <= y <= 456:
                if 378 <= x <= 655: self.check_update()
                elif 680 <= x <= 970:
                    if self.update_downloading or self.update_waiting:
                        self.update_cancel.set()
                        self.update_message = "Отменяю…"
                        self.draw()
                    elif self.update_file is not None: self.install_update()
                    else: self.download_update()
                elif 995 <= x <= 1310:
                    webbrowser.open('https://github.com/wilmain-arch/RemoteVibecode/releases/latest')
            elif self.page == "Настройки" and 378 <= x <= 624 and 778 <= y <= 837:
                self.save_settings()

        canvas.bind("<Configure>", fit)
        canvas.bind("<Button-1>", click)

    def switch(self, page):
        if self.page == "Настройки" and self.form:
            self.form_draft = {key: field.get() for key, field in self.form.items()}
        self.page = page
        self.message = ""
        self.draw()

    def auto_update_check(self):
        self.check_update(manual=False)
        self.root.after(3_600_000, self.auto_update_check)

    def save_update_status(self, attempt=None):
        self.update_cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            old = json.loads(self.update_cache.read_text(encoding="utf-8"))
        except (OSError, ValueError): old = {}
        if not isinstance(old, dict): old = {}
        data = {"result": self.update_info, "checked": self.update_checked,
                "message": self.update_message, "attempt": attempt or old.get("attempt", 0)}
        temporary = self.update_cache.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        os.replace(temporary,self.update_cache)

    def check_update(self, manual=True):
        if self.update_busy or self.update_downloading or self.update_waiting: return
        try:
            last = json.loads(self.update_cache.read_text(encoding="utf-8")).get("attempt", 0)
        except (OSError, ValueError, TypeError, AttributeError): last = 0
        if not manual and time.time() - last < updates.DAY: return
        self.update_busy = True
        self.update_message = "Проверяю обновления…"
        try: self.save_update_status(attempt=time.time())
        except OSError: pass
        self.draw()
        def work():
            try: self.events.put(("update", "checked", updates.check()))
            except Exception as exc: self.events.put(("update", "error", f"Не удалось проверить: {exc}"))
        threading.Thread(target=work,daemon=True).start()

    def download_update(self):
        if self.update_busy or self.update_downloading or self.update_waiting: return
        self.update_downloading = True
        self.update_cancel.clear()
        self.update_message = "Проверяю релиз и скачиваю файл…"
        self.draw()
        def work():
            try:
                # Never install from unsigned mutable local UI cache.
                result = updates.check()
                if not result['available']: raise RuntimeError('Установлена актуальная версия')
                previous = [-1]
                def progress(percent):
                    if previous[0] != percent:
                        previous[0] = percent
                        self.events.put(("update", "progress", percent))
                path = updates.download(result['info'], self.config_path.parent / 'updates', progress, self.update_cancel)
                self.events.put(("update", "downloaded", (path,result)))
            except Exception as exc: self.events.put(("update", "error", str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def release_update_lock(self):
        if not self.started: return
        def release():
            try:
                state = json.loads((self.config_path.parent / 'state.json').read_text(encoding='utf-8'))
                req = request.Request(f"https://127.0.0.1:{self.config['bridgePort']}/api/update/prepare",
                    data=b'{"release":true}', method='POST', headers={
                        'Authorization':'Bearer '+state['token'], 'Content-Type':'application/json'})
                with request.urlopen(req, context=ssl._create_unverified_context(), timeout=5): pass
            except Exception: pass  # Lease expires automatically in 120 seconds.
        threading.Thread(target=release,daemon=True).start()

    def install_update(self):
        if self.update_waiting or self.update_file is None: return
        self.update_waiting = True
        self.update_cancel.clear()
        self.update_message = "Проверяю задачи перед установкой…"
        self.draw()
        def work():
            try:
                while not self.update_cancel.is_set():
                    if not self.started:
                        self.events.put(("update", "install", None)); return
                    state = json.loads((self.config_path.parent / 'state.json').read_text(encoding='utf-8'))
                    req = request.Request(f"https://127.0.0.1:{self.config['bridgePort']}/api/update/prepare",
                        data=b'{"arm":true}',method='POST',headers={'Authorization':'Bearer '+state['token'], 'Content-Type':'application/json'})
                    with request.urlopen(req,context=ssl._create_unverified_context(),timeout=30) as response:
                        status = json.load(response)
                    if status.get('ready') and status.get('armed'):
                        self.events.put(("update", "install", None)); return
                    self.events.put(("update", "waiting", f"Жду завершения задач: {status.get('active', 0)} · В очереди: {status.get('queued', 0)}"))
                    self.update_cancel.wait(5)
                self.events.put(("update", "error", "Установка отменена. Скачанный файл сохранён."))
            except Exception as exc:
                self.events.put(("update", "error", f"Установка отложена: не удалось подтвердить завершение задач ({exc})"))
        threading.Thread(target=work,daemon=True).start()

    def copy_address(self, address):
        self.root.clipboard_clear()
        self.root.clipboard_append(address)
        self.message = "Адрес скопирован"

    def unpair(self):
        try:
            state = json.loads((self.config_path.parent / "state.json").read_text(encoding="utf-8"))
            token = state["token"]
            port = self.config["bridgePort"]
            url = f"https://127.0.0.1:{port}/api/unpair"
            req = request.Request(url, data=b"{}", method="POST",
                                  headers={"Authorization": "Bearer " + token,
                                           "Content-Type": "application/json"})
            with request.urlopen(req, context=ssl._create_unverified_context(), timeout=5):
                pass
            self.message = "Телефон отключён. Для новой привязки перезапустите агент."
            self.refresh_pairing(redraw=False)
        except Exception as exc:
            self.message = f"Не удалось отключить телефон: {exc}"
        self.draw()

    def refresh_pairing(self, redraw=True):
        try:
            state = json.loads((self.config_path.parent / "state.json").read_text(encoding="utf-8"))
            token = state["token"]
            port = self.config["bridgePort"]
            req = request.Request(f"https://127.0.0.1:{port}/api/pairing/rotate",
                                  data=b"{}", method="POST",
                                  headers={"Authorization": "Bearer " + token,
                                           "Content-Type": "application/json"})
            with request.urlopen(req, context=ssl._create_unverified_context(), timeout=5) as response:
                pin = json.load(response)["pin"]
            _, _, fingerprint = ensure_certificate(self.config_path.parent)
            previous = Path(self.pairing_path) if self.pairing_path else None
            self.pairing_path = str(pairing_image(self.config, fingerprint, pin, self.config_path.parent))
            if previous:
                previous.unlink(missing_ok=True)
            self.message = "Новый QR-код готов. Он действует 30 минут."
        except Exception as exc:
            self.error = f"Не удалось создать QR-код: {exc}"
        if redraw:
            self.draw()

    def save_settings(self):
        data = {key: field.get().strip() for key, field in self.form.items()}
        data.update({"publicPort": 8765, "controlPort": 8766, "dataPort": 8767, "bridgePort": 18765})
        try:
            validated = load_config_from_dict(data)
            if self.started and validated != self.config:
                self.message = "Настройки сохранены. Перезапустите агент, чтобы применить новый сервер."
            else:
                self.message = "Настройки сохранены."
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(validated, indent=2), encoding="utf-8")
            if sys.platform != "win32":
                temporary.chmod(0o600)
            os.replace(temporary, self.config_path)
            self.config = validated
            self.form_draft = dict(data)
            self.configured = True
            self.error = ""
            if not self.started:
                self.start_agent()
                self.page = "Обзор"
        except Exception as exc:
            self.message = ""
            self.error = str(exc)
        self.draw()


def launch(config_path: Path, update_ready: Path | None = None):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = (config_path.parent / "agent.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    app = AgentWindow(config_path)
    if update_ready is not None:
        def confirm_start():
            if not app.configured or app.bridge_ready:
                update_ready.write_text("ready", encoding="utf-8")
            elif not app.error:
                app.root.after(500, confirm_start)
        app.root.after(2000, confirm_start)
    app.root.mainloop()
    log_file.close()
