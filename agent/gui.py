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

from agent.dashboard import WIDTH as DASH_WIDTH, HEIGHT as DASH_HEIGHT, render_dashboard
from agent.windows_agent import ensure_certificate, load_config, load_config_from_dict, pairing_image, run_agent


BG = "#080d19"
SIDE = "#0d1423"
CARD = "#111a2b"
CARD_2 = "#172238"
STROKE = "#25314a"
WHITE = "#f5f7ff"
MUTED = "#a0afd1"
QUIET = "#7182a7"
GREEN = "#12d99b"
BLUE = "#527cff"
CYAN = "#04d1ed"
FONT = "Segoe UI"


class AgentWindow:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.root = tk.Tk()
        self.root.title("RemoteVibecode")
        self.root.geometry("1500x845")
        self.root.minsize(1040, 620)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[tuple] = queue.Queue()
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
        self.root.after(250, self.poll)
        if self.configured:
            self.start_agent()

    def close(self):
        self.stop.set()
        self.root.destroy()

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

    def text(self, parent, value, size=13, color=WHITE, weight="normal", **options):
        return tk.Label(parent, text=value, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, weight), anchor="w", **options)

    def button(self, parent, label, command, primary=False, width=None):
        return tk.Button(parent, text=label, command=command, cursor="hand2",
                         font=(FONT, 12, "bold" if primary else "normal"),
                         fg=WHITE, bg=BLUE if primary else CARD_2,
                         activeforeground=WHITE, activebackground="#4165e9" if primary else STROKE,
                         bd=0, relief="flat", padx=19, pady=12, width=width)

    def card(self, parent, padx=25, pady=23):
        outer = tk.Frame(parent, bg=STROKE, padx=1, pady=1)
        inner = tk.Frame(outer, bg=CARD, padx=padx, pady=pady)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def draw(self):
        for child in self.root.winfo_children():
            child.destroy()
        if self.page == "Обзор":
            self.draw_dashboard()
            return
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=18, pady=18)
        sidebar = tk.Frame(shell, bg=SIDE, width=235, padx=16, pady=22,
                           highlightbackground=STROKE, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDE)
        brand.pack(fill="x", pady=(10, 58))
        try:
            icon_path = Path(__file__).with_name("remotevibecode.ico")
            with Image.open(icon_path) as source:
                self.brand_image = ImageTk.PhotoImage(source.convert("RGBA").resize((44, 44)))
            tk.Label(brand, image=self.brand_image, bg=SIDE).pack(side="left")
            self.root.iconphoto(True, self.brand_image)
        except OSError:
            self.text(brand, "›_))", 24, CYAN, "bold").pack(side="left")
        self.text(brand, "RemoteVibecode", 13, WHITE, "bold").pack(side="left", padx=(9, 0))
        for icon, label in [("⌂", "Обзор"), ("▯", "Устройства"),
                            ("⌁", "Подключение"), ("⚙", "Настройки")]:
            selected = self.page == label
            nav = tk.Button(sidebar, text=f"{icon}     {label}", anchor="w", cursor="hand2",
                            command=lambda name=label: self.switch(name),
                            font=(FONT, 13, "bold" if selected else "normal"),
                            bg="#1a2850" if selected else SIDE,
                            fg=WHITE if selected else MUTED,
                            activebackground=CARD_2, activeforeground=WHITE,
                            relief="flat", bd=0, padx=15, pady=15)
            nav.pack(fill="x", pady=3)
        self.text(sidebar, "АГЕНТ REMOTEVIBECODE", 9, QUIET).pack(side="bottom", anchor="w")
        content = tk.Frame(shell, bg=BG)
        content.pack(side="left", fill="both", expand=True, padx=(27, 0))
        if self.page == "Обзор":
            self.overview(content)
        elif self.page == "Устройства":
            self.devices(content)
        elif self.page == "Подключение":
            self.connection(content)
        else:
            self.settings(content)

    def draw_dashboard(self):
        canvas = tk.Canvas(self.root, bg=BG, bd=0, highlightthickness=0, cursor="arrow")
        canvas.pack(fill="both", expand=True)
        base = render_dashboard(
            icon_path=Path(__file__).with_name("remotevibecode.ico"),
            qr_path=Path(self.pairing_path) if self.pairing_path else None,
            configured=self.configured, bridge_ready=self.bridge_ready,
            relay_ready=self.relay_ready, paired=self.paired(),
            address=(f"{self.config['relayHost']}:{self.config['publicPort']}"
                     if self.configured else ""),
            error=self.error, status=self.message,
        )

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
            canvas._layout = ((width - target[0]) // 2, (height - target[1]) // 2, scale)

        def click(event):
            x0, y0, scale = getattr(canvas, "_layout", (0, 0, 1))
            x, y = (event.x - x0) / scale, (event.y - y0) / scale
            if 32 <= x <= 308 and 183 <= y <= 468:
                index = int((y - 183) // 72)
                pages = ("Обзор", "Устройства", "Подключение", "Настройки")
                if 0 <= index < len(pages):
                    self.switch(pages[index])
            elif 650 <= x <= 1022 and 419 <= y <= 477:
                if self.bridge_ready:
                    self.refresh_pairing()
                elif not self.configured:
                    self.switch("Настройки")
            elif 1384 <= x <= 1619 and 564 <= y <= 607:
                self.switch("Устройства")
            elif 1387 <= x <= 1593 and 650 <= y <= 709:
                self.switch("Устройства")
            elif 1427 <= x <= 1597 and 827 <= y <= 879 and self.configured:
                address = f"https://{self.config['relayHost']}:{self.config['publicPort']}"
                self.copy_address(address)
                self.draw()

        canvas.bind("<Configure>", fit)
        canvas.bind("<Button-1>", click)

    def switch(self, page):
        self.page = page
        self.message = ""
        self.draw()

    def heading(self, parent, title, subtitle):
        self.text(parent, title, 31, WHITE, "bold").pack(anchor="w", pady=(5, 4))
        self.text(parent, subtitle, 12, GREEN if self.bridge_ready and self.relay_ready else MUTED).pack(anchor="w", pady=(0, 20))

    def overview(self, parent):
        paired = self.paired()
        if not self.configured:
            self.heading(parent, "Подключите свой сервер", "Настройка займёт несколько минут")
            self.setup_form(parent)
            return
        ready = self.bridge_ready and self.relay_ready
        title = "Ваш компьютер готов" if ready else "Подключаем компьютер"
        subtitle = "●  Мост и сервер работают" if ready else "●  Проверяем локальный мост и связь с сервером"
        self.heading(parent, title, subtitle)
        if self.error:
            self.text(parent, self.error[:125], 11, "#ffafae", wraplength=840).pack(anchor="w", pady=(0, 12))
        row = tk.Frame(parent, bg=BG, height=420)
        row.pack(fill="x")
        row.pack_propagate(False)
        left_outer, left = self.card(row)
        left_outer.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right_outer, right = self.card(row)
        right_outer.pack(side="left", fill="both", expand=True, padx=(0, 0))
        self.text(left, "Подключить телефон" if not paired else "Телефон подключён", 20, WHITE, "bold").pack(anchor="w")
        self.text(left, "Откройте RemoteVibecode на телефоне\nи отсканируйте код привязки." if not paired else
                  "Телефон привязан к этому компьютеру.", 12, MUTED, justify="left").pack(anchor="w", pady=(10, 13))
        qr = Path(self.pairing_path)
        if not paired and self.pairing_path and qr.is_file():
            try:
                with Image.open(qr) as source:
                    picture = source.convert("RGB").resize((192, 192))
                self.qr_image = ImageTk.PhotoImage(picture)
                tk.Label(left, image=self.qr_image, bg=WHITE, padx=8, pady=8).pack(anchor="w", pady=(4, 12))
            except OSError:
                self.text(left, "QR-код недоступен", 12, MUTED).pack(anchor="w", pady=30)
        elif not paired:
            self.text(left, "QR появится после запуска локального моста", 12, MUTED,
                      wraplength=350).pack(anchor="w", pady=55)
        else:
            self.text(left, "●  Привязка активна", 15, GREEN).pack(anchor="w", pady=65)
        if not paired:
            self.text(left, "Код действует 30 минут", 11, QUIET).pack(anchor="w")
            if self.bridge_ready:
                self.button(left, "Создать новый код", self.refresh_pairing, True).pack(anchor="w", pady=(13, 0))
        self.text(right, "Статус подключения", 20, WHITE, "bold").pack(anchor="w", pady=(0, 25))
        for title, ok, description in [("Codex", self.bridge_ready, "Локальный мост"),
                                       ("Мост", self.relay_ready, "Связь с сервером"),
                                       ("Телефон", paired, "Привязка устройства")]:
            item = tk.Frame(right, bg=CARD_2, padx=15, pady=13)
            item.pack(fill="x", pady=4)
            self.text(item, "●", 16, GREEN if ok else QUIET).pack(side="left")
            labels = tk.Frame(item, bg=CARD_2)
            labels.pack(side="left", padx=12)
            self.text(labels, title, 13, WHITE, "bold").pack(anchor="w")
            self.text(labels, description + (" · активен" if ok else " · ожидание"), 10, MUTED).pack(anchor="w")
        self.text(right, "TLS · проверка сертификата", 11, MUTED).pack(anchor="w", pady=(15, 0))
        lower_outer, lower = self.card(parent, 24, 12)
        lower_outer.pack(fill="both", expand=True, pady=(14, 0))
        top = tk.Frame(lower, bg=CARD)
        top.pack(fill="x")
        self.text(top, "Привязанные устройства", 17, WHITE, "bold").pack(side="left")
        self.button(top, "Устройства  →", lambda: self.switch("Устройства")).pack(side="right")
        self.text(lower, "●  Телефон привязан" if paired else "Пока нет привязанных телефонов",
                  12, GREEN if paired else MUTED).pack(anchor="w", pady=(12, 0))
        address_row = tk.Frame(lower, bg=CARD)
        address_row.pack(fill="x", pady=(12, 0))
        address_labels = tk.Frame(address_row, bg=CARD)
        address_labels.pack(side="left", fill="x", expand=True)
        public_address = f"https://{self.config['relayHost']}:{self.config['publicPort']}"
        self.text(address_labels, "Адрес подключения  ·  " + public_address, 12, MUTED).pack(anchor="w")
        self.button(address_row, "Копировать", lambda: self.copy_address(public_address)).pack(side="right")

    def copy_address(self, address):
        self.root.clipboard_clear()
        self.root.clipboard_append(address)
        self.message = "Адрес скопирован"

    def devices(self, parent):
        self.heading(parent, "Устройства", "Управляйте привязкой телефона")
        outer, card = self.card(parent)
        outer.pack(fill="x")
        if self.paired():
            self.text(card, "●  Привязанный телефон", 18, WHITE, "bold").pack(anchor="w")
            self.text(card, "Мост не получает модель или имя телефона; устройство показано без вымышленного названия.",
                      11, MUTED, wraplength=700).pack(anchor="w", pady=(8, 22))
            self.button(card, "Отключить телефон", self.unpair).pack(anchor="w")
        else:
            self.text(card, "Нет привязанных устройств", 18, WHITE, "bold").pack(anchor="w")
            self.text(card, "Для привязки отсканируйте QR-код на экране обзора.", 12, MUTED).pack(anchor="w", pady=12)
            self.button(card, "Перейти к QR-коду", lambda: self.switch("Обзор"), True).pack(anchor="w")
        if self.message:
            self.text(parent, self.message, 11, MUTED).pack(anchor="w", pady=15)

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

    def connection(self, parent):
        self.heading(parent, "Подключение", "Ваш сервер и состояние канала")
        outer, card = self.card(parent)
        outer.pack(fill="x")
        if self.configured:
            self.text(card, "Адрес сервера", 13, MUTED).pack(anchor="w")
            self.text(card, f"{self.config['relayHost']}:{self.config['publicPort']}", 20, WHITE, "bold").pack(anchor="w", pady=(4, 22))
            self.text(card, "●  Сервер доступен" if self.relay_ready else "○  Нет связи с сервером",
                      14, GREEN if self.relay_ready else MUTED).pack(anchor="w")
            self.text(card, "Подключение идёт с компьютера к вашему серверу. Входящий порт на ПК не нужен.",
                      12, MUTED, wraplength=730).pack(anchor="w", pady=(18, 0))
        else:
            self.text(card, "Сервер ещё не настроен", 17, MUTED).pack(anchor="w")
        if self.error:
            self.text(parent, self.error, 11, "#ffafae", wraplength=750).pack(anchor="w", pady=17)

    def settings(self, parent):
        self.heading(parent, "Настройки", "Адрес сервера и локальный Codex")
        self.setup_form(parent)

    def setup_form(self, parent):
        outer, card = self.card(parent)
        outer.pack(fill="x")
        self.form = {}
        fields = [
            ("relayHost", "Адрес вашего сервера", "Домен или IPv4 без порта", False),
            ("relayFingerprint", "SHA-256 отпечаток сертификата", "64 шестнадцатеричных символа", False),
            ("relaySecret", "Секрет сервера", "Из /etc/remotevibecode/relay-secret", True),
            ("codexExecutable", "Путь к Codex CLI", "Например, C:\\Users\\...\\codex.exe", False),
        ]
        for key, label, hint, secret in fields:
            self.text(card, label, 12, WHITE, "bold").pack(anchor="w", pady=(0, 5))
            line = tk.Frame(card, bg=CARD_2, padx=12, pady=3)
            line.pack(fill="x", pady=(0, 4))
            entry = tk.Entry(line, bg=CARD_2, fg=WHITE, insertbackground=WHITE, bd=0,
                             relief="flat", font=(FONT, 12), show="•" if secret else "")
            entry.pack(fill="x", ipady=8)
            if key == "relaySecret" and self.configured:
                entry.insert(0, self.config.get(key, ""))
            else:
                entry.insert(0, str(self.config.get(key, "")))
            self.form[key] = entry
            self.text(card, hint, 10, QUIET).pack(anchor="w", pady=(0, 15))
        self.button(card, "Сохранить настройки", self.save_settings, True).pack(anchor="w")
        if self.message or self.error:
            self.text(parent, self.message or self.error, 11, GREEN if self.message else "#ffafae",
                      wraplength=780).pack(anchor="w", pady=15)
        self.text(parent, "Отпечаток и секрет получите на собственном сервере. Они не отправляются разработчику приложения.",
                  11, MUTED, wraplength=800).pack(anchor="w", pady=(15, 0))

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
