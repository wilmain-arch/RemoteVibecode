"""Desktop control panel for the Windows bridge agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import ssl
import sys
import threading
import tkinter as tk
from urllib import request

from PIL import Image, ImageTk
import pystray

from agent.dashboard import WIDTH as DASH_WIDTH, HEIGHT as DASH_HEIGHT, render_dashboard, render_page
from agent.windows_agent import ensure_certificate, load_config, load_config_from_dict, pairing_image, run_agent


BG = "#080e1a"
STROKE = "#25314a"
WHITE = "#f5f7ff"
BLUE = "#527cff"
FONT = "Segoe UI"


class AgentWindow:
    def __init__(self, config_path: Path):
        self.config_path = config_path
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
            if event[0] == "bridge":
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
        if self.page == "Обзор":
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
            if 32 <= x <= 308 and 183 <= y <= 468:
                index = int((y - 183) // 72)
                pages = ("Обзор", "Устройства", "Подключение", "Настройки")
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


def launch(config_path: Path):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = (config_path.parent / "agent.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    app = AgentWindow(config_path)
    app.root.mainloop()
    log_file.close()
