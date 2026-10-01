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
import tempfile
from datetime import datetime
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from urllib import request

from PIL import Image, ImageTk
import pystray

from agent.dashboard import COLORS, FONT, SPACE, TYPE
from agent.ui_components import AppButton, ScrollableFrame, Surface, apply_theme, body_label, card, heading, icon_photo
from agent import updates
from agent.windows_agent import ensure_certificate, load_config, load_config_from_dict, pairing_image, run_agent


BG = COLORS["bg"]
STROKE = COLORS["edge"]
WHITE = COLORS["white"]
BLUE = COLORS["blue"]


class AgentWindow:
    def __init__(self, config_path: Path, *, offline_preview: bool = False):
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
        self.root.geometry("1320x820")
        self.root.minsize(900, 600)
        apply_theme(self.root)
        icon_path = Path(__file__).with_name("remotevibecode.ico")
        with Image.open(icon_path) as source:
            self.window_icon = ImageTk.PhotoImage(source.convert("RGBA").resize((64, 64)))
        self.root.iconphoto(True, self.window_icon)
        if sys.platform == "win32":
            self.root.iconbitmap(default=str(icon_path))
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy if offline_preview else self.close)
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
        self.configured = True if offline_preview else self.config_path.is_file()
        self.config = ({"relayHost": "demo.invalid", "relayFingerprint": "DEMO-FINGERPRINT",
                        "relaySecret": "DEMO-ONLY-DO-NOT-USE", "codexExecutable": "codex",
                        "publicPort": 8765, "controlPort": 8766, "dataPort": 8767,
                        "bridgePort": 18765} if offline_preview else {})
        if self.configured and not offline_preview:
            try:
                self.config = load_config(self.config_path)
            except Exception as exc:
                self.error = f"Ошибка настроек: {exc}"
                self.configured = False
        self.last_paired = self.paired()
        self.draw()
        self.root.bind_all("<Alt-KeyPress>", self._nav_hotkey)
        if not offline_preview:
            self.start_tray()
            self.root.after(250, self.poll)
            if self.configured:
                self.start_agent()
            self.root.after(3000, lambda: self.check_update(manual=False))
            self.root.after(3_600_000, self.auto_update_check)

    def close(self):
        if self.offline_preview:
            self.root.destroy()
            return
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
        if self.offline_preview:
            return
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
                    previous_hash = (self.update_info or {}).get('info', {}).get('sha256')
                    if previous_hash != value['info']['sha256']:
                        self.update_file = None
                    self.update_info = value
                    self.update_busy = False
                    self.update_error = ""
                    self.update_checked = datetime.now().strftime("%d.%m.%Y %H:%M")
                    self.update_message = (f"Доступна версия {value['info']['version']}" if value['available'] else "Установлена актуальная версия")
                    try: self.save_update_status()
                    except (OSError, ValueError, TypeError, AttributeError) as exc:
                        print(f"Не удалось сохранить кэш обновлений: {exc}", flush=True)
                elif kind == "error":
                    self.update_busy = self.update_downloading = self.update_waiting = False
                    self.update_error = value
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
        if self.offline_preview:
            return False
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
        self._build_shell()
        self._build_page()
        self.scroll.bind_descendants()

    def _build_shell(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        sidebar = tk.Frame(self.root, bg=COLORS["sidebar"], padx=16, pady=18, width=226)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        content = tk.Frame(self.root, bg=COLORS["bg"], padx=24, pady=18)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
        brand.pack(fill="x", pady=(4, 26))
        icon_path = Path(__file__).with_name("remotevibecode.ico")
        with Image.open(icon_path) as source:
            icon = source.convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS)
        self.brand_image = ImageTk.PhotoImage(icon)
        tk.Label(brand, image=self.brand_image, bg=COLORS["sidebar"]).pack(side="left", padx=(0, 10))
        tk.Label(brand, text="RemoteVibecode", bg=COLORS["sidebar"], fg=COLORS["white"],
                 font=(FONT, 12, "bold")).pack(side="left")

        nav = tk.Frame(sidebar, bg=COLORS["sidebar"])
        nav.pack(fill="x")
        pages = (("home", "Обзор"), ("phone", "Устройства"), ("link", "Подключение"),
                 ("settings", "Настройки"), ("updates", "Обновления"))
        self.nav_buttons = {}
        self.nav_images = []
        for symbol, name in pages:
            label = name
            if name == "Обновления" and self.update_info and self.update_info.get("available"):
                label += "   •"
            button = AppButton(nav, label, lambda target=name: self.switch(target), compact=True)
            image = icon_photo(self.root, symbol, size=23,
                               color=COLORS["white"] if self.page == name else COLORS["muted"])
            self.nav_images.append(image)
            button.configure(image=image, compound="left", anchor="w", justify="left", padx=13, pady=12,
                             bg=COLORS["panel_raised"] if self.page == name else COLORS["sidebar"],
                             highlightbackground=COLORS["sidebar"])
            button.base_bg = COLORS["panel_raised"] if self.page == name else COLORS["sidebar"]
            button.hover_bg = COLORS["panel_hover"]
            button.pack(fill="x", pady=3)
            self.nav_buttons[name] = button

        footer = tk.Frame(sidebar, bg=COLORS["sidebar"])
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        tk.Label(footer, text="Закрытие окна — в трей", bg=COLORS["sidebar"],
                 fg=COLORS["dim"], font=(FONT, TYPE["small"])).pack(anchor="w")

        self.page_title = tk.Label(content, text="", bg=COLORS["bg"], fg=COLORS["white"],
                                   font=(FONT, TYPE["page"], "bold"), anchor="w")
        self.page_title.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.scroll = ScrollableFrame(content, background=COLORS["bg"])
        self.scroll.grid(row=1, column=0, sticky="nsew")

    def _nav_hotkey(self, event):
        pages = {"1": "Обзор", "2": "Устройства", "3": "Подключение",
                 "4": "Настройки", "5": "Обновления"}
        page = pages.get(event.keysym)
        if page:
            self.switch(page)
            return "break"

    def _build_page(self):
        titles = {
            "Обзор": "Ваш компьютер готов" if self.configured and self.bridge_ready and self.relay_ready else "Обзор",
            "Устройства": "Устройства",
            "Подключение": "Подключение",
            "Настройки": "Настройки",
            "Обновления": "Обновления",
        }
        self.page_title.configure(text=titles.get(self.page, self.page))
        body = self.scroll.content
        if self.page == "Обзор":
            self._page_overview(body)
        elif self.page == "Устройства":
            self._page_devices(body)
        elif self.page == "Подключение":
            self._page_connection(body)
        elif self.page == "Настройки":
            self._page_settings(body)
        elif self.page == "Обновления":
            self._page_updates(body)
        if self.error:
            self._notice(body, "Не удалось выполнить действие", self.error, error=True)
        elif self.message:
            self._notice(body, "Состояние", self.message)

    def _page_heading(self, parent, subtitle):
        label = body_label(parent, subtitle, muted=True, background=COLORS["bg"], wraplength=900)
        label.pack(fill="x", pady=(0, 16))
        self._adapt_wrap(label, 900)

    def _adapt_wrap(self, widget, maximum):
        def set_wrap(event):
            width = max(180, min(maximum, event.width - 12))
            if int(widget.cget("wraplength") or 0) != width:
                widget.configure(wraplength=width)
        widget.bind("<Configure>", set_wrap, add="+")

    def _notice(self, parent, title, text, *, error=False):
        surface = Surface(parent)
        surface.pack(fill="x", pady=(12, 0))
        inner = surface.body
        bg = COLORS["error_bg"] if error else COLORS["panel"]
        inner.configure(bg=bg)
        tk.Label(inner, text=title, bg=bg,
                 fg=COLORS["error_fg"] if error else COLORS["green"],
                 font=(FONT, TYPE["section"], "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 5))
        row = tk.Frame(inner, bg=bg)
        row.pack(fill="x", padx=16, pady=(0, 12))
        details = ScrolledText(row, height=3, wrap="word", undo=False, takefocus=True,
                               bg=COLORS["panel_raised"], fg=COLORS["error_fg"] if error else COLORS["muted"],
                               insertbackground=COLORS["white"], relief="flat", bd=0,
                               highlightthickness=1, highlightbackground=COLORS["edge"],
                               highlightcolor=COLORS["focus"],
                               font=(FONT, TYPE["body"]), padx=9, pady=7)
        self._style_text_scrollbar(details)
        details.insert("1.0", text)
        details.configure(state="disabled")
        details.pack(side="left", fill="both", expand=True)
        AppButton(row, "Копировать", lambda value=text: self.copy_text(value), compact=True).pack(
            side="left", padx=(10, 0), anchor="n")

    def _card(self, parent, title, subtitle=None, *, padding=18):
        surface, inner = card(parent, padding=padding)
        surface.pack(fill="both", expand=True, pady=(0, 14))
        heading(inner, title).pack(anchor="w")
        if subtitle:
            body_label(inner, subtitle, muted=True, wraplength=950).pack(anchor="w", pady=(5, 12))
        else:
            tk.Frame(inner, bg=COLORS["panel"], height=10).pack(fill="x")
        return surface, inner

    def _style_text_scrollbar(self, text_widget):
        scrollbar = getattr(text_widget, "vbar", None)
        if scrollbar is not None:
            scrollbar.configure(bg=COLORS["panel_raised"], troughcolor=COLORS["bg"],
                                activebackground=COLORS["blue"], relief="flat", bd=0,
                                highlightthickness=0, width=11)

    def _two_columns(self, parent, left, right, *, breakpoint=1050):
        parent.grid_columnconfigure(0, weight=11, uniform="columns")
        parent.grid_columnconfigure(1, weight=9, uniform="columns")
        def layout(event=None):
            width = event.width if event is not None else parent.winfo_width()
            if width >= breakpoint:
                left.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 9), pady=(0, 16))
                right.grid(row=0, column=1, columnspan=1, sticky="nsew", padx=(9, 0), pady=(0, 16))
            else:
                left.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
                right.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 16))
        parent.bind("<Configure>", layout, add="+")
        parent.after_idle(layout)

    def _status_row(self, parent, name, description, active):
        row = tk.Frame(parent, bg=COLORS["panel"], padx=13, pady=12)
        row.pack(fill="x", pady=4)
        tk.Label(row, text="●", bg=COLORS["panel"],
                 fg=COLORS["green"] if active else COLORS["dim"],
                 font=(FONT, 13, "bold")).pack(side="left", padx=(0, 10))
        text = tk.Frame(row, bg=COLORS["panel"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=name, bg=COLORS["panel"], fg=COLORS["white"],
                 font=(FONT, TYPE["body"], "bold"), anchor="w").pack(fill="x")
        tk.Label(text, text=description, bg=COLORS["panel"], fg=COLORS["muted"],
                 font=(FONT, TYPE["small"]), anchor="w").pack(fill="x", pady=(2, 0))

    def _phone_icon(self, parent):
        picture = icon_photo(self.root, "phone", size=30, color=COLORS["muted"])
        label = tk.Label(parent, image=picture, bg=COLORS["panel_raised"])
        label.image = picture
        label.pack(side="left", padx=(0, 16))

    def _status_tiles(self, parent, items):
        strip = tk.Frame(parent, bg=COLORS["panel"])
        strip.pack(fill="x", pady=(12, 16))
        for index, (symbol, name, description, active) in enumerate(items):
            strip.grid_columnconfigure(index, weight=1, uniform="status")
            tile = tk.Frame(strip, bg=COLORS["panel"])
            tile.grid(row=0, column=index, sticky="nsew", padx=8)
            picture = icon_photo(self.root, symbol, size=44,
                                 color=COLORS["white"] if active else COLORS["dim"])
            icon = tk.Label(tile, image=picture, bg=COLORS["panel_raised"], padx=18, pady=18)
            icon.image = picture
            icon.pack(pady=(0, 12))
            tk.Label(tile, text="●  " + name, bg=COLORS["panel"],
                     fg=COLORS["green"] if active else COLORS["muted"],
                     font=(FONT, TYPE["body"], "bold")).pack()
            tk.Label(tile, text=description, bg=COLORS["panel"], fg=COLORS["muted"],
                     font=(FONT, TYPE["small"])).pack(pady=(4, 0))

    def _page_overview(self, parent):
        if not self.configured:
            self._page_heading(parent, "Настройте сервер и локальный Codex, чтобы подключить компьютер к телефону.")
        else:
            ready = self.bridge_ready and self.relay_ready
            self._page_heading(parent, "Codex подключён" if ready else "Проверяем соединение с Codex и сервером.")
        top = tk.Frame(parent, bg=COLORS["bg"])
        top.pack(fill="x")
        pair, pair_body = card(top, padding=18)
        status, status_body = card(top, padding=18)
        pair_body.grid_columnconfigure(1, weight=1)
        qr_box = tk.Frame(pair_body, bg=COLORS["panel_raised"], width=174, height=174,
                          highlightthickness=0)
        qr_box.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 18))
        qr_box.grid_propagate(False)
        path = Path(self.pairing_path) if self.pairing_path else None
        if not self.offline_preview and not self.paired() and path and path.is_file():
            try:
                with Image.open(path) as source:
                    source = source.convert("RGB").resize((164, 164), Image.Resampling.NEAREST)
                self.qr_image = ImageTk.PhotoImage(source)
                tk.Label(qr_box, image=self.qr_image, bg=COLORS["white"]).place(relx=.5, rely=.5, anchor="center")
            except OSError:
                tk.Label(qr_box, text="QR-код\nнедоступен", bg=COLORS["white"], fg=COLORS["panel"],
                         font=(FONT, TYPE["body"], "bold")).place(relx=.5, rely=.5, anchor="center")
        else:
            self.pairing_badge = icon_photo(self.root, "phone", size=64, color=COLORS["green"])
            tk.Label(qr_box, image=self.pairing_badge, bg=COLORS["panel_raised"]).place(
                relx=.5, rely=.40, anchor="center")
            tk.Label(qr_box, text="Привязан" if self.paired() else "Подключить", bg=COLORS["panel_raised"],
                     fg=COLORS["muted"], font=(FONT, TYPE["body"])).place(
                         relx=.5, rely=.74, anchor="center")

        tk.Label(pair_body, text="Телефон подключён" if self.paired() else "Подключить телефон",
                 bg=COLORS["panel"], fg=COLORS["white"], font=(FONT, TYPE["section"], "bold"),
                 anchor="w").grid(row=0, column=1, sticky="ew")
        detail = tk.Label(pair_body, text=("Телефон привязан к этому ПК" if self.paired() else
                                           "Откройте RemoteVibecode на телефоне и отсканируйте QR-код."),
                 bg=COLORS["panel"], fg=COLORS["muted"], font=(FONT, TYPE["body"]),
                 anchor="w", justify="left", wraplength=440)
        detail.grid(row=1, column=1, sticky="ew", pady=(8, 5))
        self._adapt_wrap(detail, 440)
        tk.Label(pair_body, text="Код действует 30 минут" if not self.paired() else "Новый код заменит текущую привязку",
                 bg=COLORS["panel"], fg=COLORS["dim"], font=(FONT, TYPE["small"]), anchor="w").grid(
                     row=2, column=1, sticky="ew")
        qr_button = AppButton(pair_body, "Настроить подключение" if not self.configured else
                              ("Создать новый QR-код" if self.paired() or path else "Показать QR-код"),
                              self.show_pairing, primary=True)
        qr_button.grid(row=3, column=1, sticky="w", pady=(14, 0))
        if self.configured and not self.bridge_ready and not self.offline_preview:
            qr_button.configure(state="disabled")

        heading(status_body, "Статус подключения").pack(anchor="w", pady=(0, 8))
        self._status_tiles(status_body, (
            ("home", "Codex", "Активен" if self.bridge_ready else "Ожидание", self.bridge_ready),
            ("link", "Сервер", "Доступен" if self.relay_ready else "Нет связи", self.relay_ready),
            ("phone", "Телефон", "Привязан" if self.paired() else "Не привязан", self.paired()),
        ))
        tk.Label(status_body, text="◇  Соединение защищено сквозным шифрованием",
                 bg=COLORS["panel_raised"], fg=COLORS["muted"], font=(FONT, TYPE["small"]),
                 padx=10, pady=8, anchor="w").pack(fill="x", pady=(7, 0))
        self._two_columns(top, pair, status, breakpoint=940)

        device_surface, device_body = self._card(parent, "Привязанные устройства")
        row = tk.Frame(device_body, bg=COLORS["panel_raised"], padx=14, pady=12)
        row.pack(fill="x")
        phone = tk.Frame(row, bg=COLORS["panel_raised"])
        phone.pack(side="left", fill="x", expand=True)
        self._phone_icon(phone)
        labels = tk.Frame(phone, bg=COLORS["panel_raised"])
        labels.pack(side="left", fill="x", expand=True)
        tk.Label(labels, text="Привязанный телефон" if self.paired() else "Телефон не привязан",
                 bg=COLORS["panel_raised"], fg=COLORS["white"],
                 font=(FONT, TYPE["body"], "bold"), anchor="w").pack(fill="x")
        tk.Label(labels, text="Подключён к этому компьютеру" if self.paired() else "Ожидает привязки",
                 bg=COLORS["panel_raised"], fg=COLORS["muted"],
                 font=(FONT, TYPE["small"]), anchor="w").pack(fill="x", pady=(4, 0))
        action = "Отключить" if self.paired() else "Показать QR-код"
        action_cmd = self.unpair if self.paired() else self.show_pairing
        AppButton(row, action, action_cmd, compact=True).pack(side="right", padx=(12, 0))

        address_surface, address_body = self._card(parent, "Адрес подключения",
                                                   "Используйте этот адрес для ручной настройки телефона.")
        address = (f"{self.config.get('relayHost', '')}:{self.config.get('publicPort', 8765)}"
                   if self.configured else "Сервер ещё не настроен")
        row = tk.Frame(address_body, bg=COLORS["panel"])
        row.pack(fill="x")
        addr = tk.Entry(row, bg=COLORS["panel_raised"], fg=COLORS["white"],
                        insertbackground=COLORS["white"], readonlybackground=COLORS["panel_raised"],
                        state="readonly", relief="flat", bd=0, font=("Consolas", TYPE["mono"]),
                        highlightthickness=1, highlightbackground=COLORS["edge"],
                        highlightcolor=COLORS["focus"])
        addr.pack(side="left", fill="x", expand=True, ipady=8)
        addr.configure(state="normal")
        addr.insert(0, address)
        addr.configure(state="readonly")
        self.address_entry = addr
        AppButton(row, "Копировать", lambda: self.copy_address(
            f"https://{self.config['relayHost']}:{self.config.get('publicPort', 8765)}") if self.configured else None,
            compact=True).pack(side="left", padx=(10, 0))

    def _page_devices(self, parent):
        self._page_heading(parent, "Управляйте привязкой телефона к этому компьютеру.")
        _, inner = self._card(parent, "Привязанные устройства")
        row = tk.Frame(inner, bg=COLORS["panel_raised"], padx=16, pady=16)
        row.pack(fill="x")
        self._phone_icon(row)
        labels = tk.Frame(row, bg=COLORS["panel_raised"])
        labels.pack(side="left", fill="x", expand=True)
        tk.Label(labels, text="Телефон привязан" if self.paired() else "Телефон не привязан",
                 bg=COLORS["panel_raised"], fg=COLORS["white"],
                 font=(FONT, TYPE["section"], "bold"), anchor="w").pack(fill="x")
        tk.Label(labels, text="Подключён к этому компьютеру" if self.paired() else
                 "Создайте QR-код и отсканируйте его в приложении RemoteVibecode.",
                 bg=COLORS["panel_raised"], fg=COLORS["muted"], font=(FONT, TYPE["body"]),
                 anchor="w", justify="left", wraplength=650).pack(fill="x", pady=(6, 0))
        AppButton(row, "Отключить" if self.paired() else "Показать QR-код",
                  self.unpair if self.paired() else self.show_pairing,
                  primary=not self.paired()).pack(side="right", padx=(16, 0))
        _, instruction = self._card(parent, "Как работает привязка",
                                    "Код действует 30 минут. Новая привязка заменит текущее устройство.")
        AppButton(instruction, "Показать QR-код", self.show_pairing, primary=True).pack(anchor="w")

    def _page_connection(self, parent):
        self._page_heading(parent, "Серверный адрес и состояние безопасного канала.")
        grid = tk.Frame(parent, bg=COLORS["bg"])
        grid.pack(fill="x")
        address_surface, address_body = card(grid, padding=18)
        status_surface, status_body = card(grid, padding=18)
        heading(address_body, "Адрес сервера").pack(anchor="w")
        tk.Label(address_body, text=(f"https://{self.config.get('relayHost', '')}:{self.config.get('publicPort', 8765)}"
                                     if self.configured else "Сервер ещё не настроен"),
                 bg=COLORS["panel_raised"], fg=COLORS["white"], font=("Consolas", TYPE["mono"]),
                 anchor="w", padx=12, pady=12, justify="left", wraplength=520).pack(fill="x", pady=(12, 10))
        AppButton(address_body, "Копировать адрес", lambda: self.copy_address(
            f"https://{self.config['relayHost']}:{self.config.get('publicPort', 8765)}") if self.configured else None,
            compact=True).pack(anchor="w")
        heading(status_body, "Состояние канала").pack(anchor="w", pady=(0, 8))
        self._status_row(status_body, "Codex на этом ПК", "Активен" if self.bridge_ready else "Ожидание", self.bridge_ready)
        self._status_row(status_body, "Ретранслятор", "Доступен" if self.relay_ready else "Нет связи", self.relay_ready)
        _, route = self._card(parent, "Маршрут подключения")
        route_label = body_label(route, "Телефон  →  ваш сервер  →  агент на ПК  →  локальный Codex",
                                 muted=True, wraplength=1000)
        route_label.pack(fill="x")
        self._adapt_wrap(route_label, 1000)
        body_label(route, "Входящий порт на компьютере не требуется.", muted=True,
                   size=TYPE["small"]).pack(anchor="w", pady=(10, 0))
        self._two_columns(grid, address_surface, status_surface, breakpoint=940)

    def _page_settings(self, parent):
        self._page_heading(parent, "Параметры хранятся локально на этом компьютере.")
        _, inner = self._card(parent, "Параметры подключения",
                              "Подготовьте домен или IPv4, SHA-256 отпечаток сертификата и секрет ретранслятора.")
        form = tk.Frame(inner, bg=COLORS["panel"])
        form.pack(fill="x")
        fields = (
            ("relayHost", "Адрес вашего сервера", "Домен или IPv4 без порта"),
            ("relayFingerprint", "SHA-256 отпечаток сертификата", "64 шестнадцатеричных символа"),
            ("relaySecret", "Секрет сервера", "Берётся из настроек ретранслятора на вашем сервере"),
            ("codexExecutable", "Исполняемый файл Codex", "Пусто — агент попробует найти Codex Desktop, затем CLI"),
        )
        form.grid_columnconfigure(0, weight=1)
        for row_index, (key, title, hint) in enumerate(fields):
            tk.Label(form, text=title, bg=COLORS["panel"], fg=COLORS["white"],
                     font=(FONT, TYPE["body"], "bold"), anchor="w").grid(
                         row=row_index * 3, column=0, sticky="ew", pady=(10, 4))
            entry = tk.Entry(form, bg=COLORS["panel_raised"], fg=COLORS["white"],
                             insertbackground=COLORS["white"], relief="flat", bd=0,
                             font=(FONT, TYPE["body"]), highlightthickness=1,
                             highlightbackground=COLORS["edge"], highlightcolor=COLORS["focus"],
                             show="•" if key == "relaySecret" else "")
            entry.insert(0, self.form_draft.get(key, str(self.config.get(key, ""))))
            entry.grid(row=row_index * 3 + 1, column=0, sticky="ew", ipady=8)
            self.form[key] = entry
            hint_label = tk.Label(form, text=hint, bg=COLORS["panel"], fg=COLORS["dim"],
                                  font=(FONT, TYPE["small"]), anchor="w", justify="left",
                                  wraplength=900)
            hint_label.grid(row=row_index * 3 + 2, column=0, sticky="ew", pady=(4, 5))
            self._adapt_wrap(hint_label, 900)
        actions = tk.Frame(form, bg=COLORS["panel"])
        actions.grid(row=12, column=0, sticky="ew", pady=(14, 0))
        AppButton(actions, "Сохранить настройки", self.save_settings, primary=True).pack(side="left")

    def _page_updates(self, parent):
        self._page_heading(parent, f"RemoteVibecode {updates.VERSION}")
        info = self.update_info or {}
        available = info.get("info", {}).get("version", "") if info.get("available") else ""
        title = f"Доступна версия {available}" if available else "Обновления из GitHub Releases"
        _, inner = self._card(parent, title)
        status = self.update_message or "Проверяем автоматически раз в сутки."
        status_label = tk.Label(inner, text=status, bg=COLORS["panel"],
                 fg=COLORS["green"] if self.update_file is not None else COLORS["muted"],
                 font=(FONT, TYPE["body"]), anchor="w", justify="left", wraplength=1000)
        status_label.pack(fill="x")
        self._adapt_wrap(status_label, 1000)
        if self.update_error:
            self._notice(parent, "Ошибка обновления", self.update_error, error=True)
        if self.update_checked:
            body_label(inner, "Проверено: " + self.update_checked, muted=True,
                       size=TYPE["small"]).pack(anchor="w", pady=(7, 0))
        if self.update_downloading:
            progress = tk.Frame(inner, bg=COLORS["panel_raised"], height=10)
            progress.pack(fill="x", pady=(12, 0))
            progress.pack_propagate(False)
            amount = tk.Frame(progress, bg=COLORS["cyan"], width=max(2, int(1000 * self.update_progress / 100)))
            amount.pack(side="left", fill="y")
        actions = tk.Frame(inner, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(14, 0))
        AppButton(actions, "Проверить обновления", self.check_update, compact=True).grid(row=0, column=0, sticky="w", padx=(0, 8))
        if available:
            if self.update_downloading or self.update_waiting:
                label, callback = "Отменить", self.cancel_update
            elif self.update_file is not None:
                label, callback = "Установить", self.install_update
            else:
                label, callback = "Скачать обновление", self.download_update
            AppButton(actions, label, callback, primary=True, compact=True).grid(row=0, column=1, sticky="w", padx=8)

        notes = (info.get("manifest", {}).get("notes", "") if info else "") or (
            "Скачивание не прерывает работу чата. Установка ждёт завершения задач и очереди сообщений.\n"
            "Windows: агент перезапустится; при неудачном запуске вернётся прежний EXE.\n"
            "Arch: установка через pacman с системным запросом прав администратора.\n"
            "Привязка телефона, настройки и файлы сохраняются.")
        _, notes_body = self._card(parent, "Что изменилось" if info.get("manifest", {}).get("notes") else "Как проходит обновление")
        for raw_line in notes.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            is_heading = line.startswith("#")
            line = line.lstrip("#").strip() if is_heading else line
            if line.startswith(("- ", "* ")):
                line = "•  " + line[2:]
            line = line.replace("**", "").replace("`", "")
            label = body_label(notes_body, line, muted=not is_heading,
                               size=TYPE["body"] if not is_heading else TYPE["section"],
                               wraplength=900)
            label.pack(fill="x", pady=(0, 12 if is_heading else 8))
            self._adapt_wrap(label, 900)
        AppButton(notes_body, "Открыть релиз на GitHub", self.open_release_notes,
                  compact=True).pack(anchor="w", pady=(12, 0))

    def switch(self, page):
        if self.page == "Настройки" and self.form:
            self.form_draft = {key: field.get() for key, field in self.form.items()}
        self.page = page
        self.message = ""
        self.draw()

    def show_pairing(self):
        if not self.configured:
            self.page = "Настройки"
            self.message = "Сначала укажите сервер и сохраните настройки."
            self.draw()
            return
        if self.offline_preview:
            self.page = "Обзор"
            self.message = "Предпросмотр: QR и реальная привязка отключены."
            self.draw()
            return
        self.page = "Обзор"
        current = Path(self.pairing_path) if self.pairing_path else None
        if not self.bridge_ready:
            self.message = "QR-код появится, когда мост Codex будет активен."
        elif not self.paired() and current and current.is_file():
            self.message = "QR-код готов к сканированию; он действует 30 минут."
        else:
            self.refresh_pairing(redraw=False)
        self.draw()

    def copy_text(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.message = "Текст скопирован"
        self.draw()

    def open_release_notes(self):
        if self.offline_preview:
            self.message = "Предпросмотр: внешний переход отключён."
            self.draw()
            return
        webbrowser.open("https://github.com/wilmain-arch/RemoteVibecode/releases/latest")

    def cancel_update(self):
        if self.offline_preview:
            self.message = "Предпросмотр: действия обновления отключены."
            self.draw()
            return
        self.update_cancel.set()
        self.update_message = "Отменяю…"
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
        if self.offline_preview:
            self.message = "Предпросмотр: проверка обновлений отключена."
            self.draw()
            return
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
        if self.offline_preview:
            self.message = "Предпросмотр: скачивание обновлений отключено."
            self.draw()
            return
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
        if self.offline_preview:
            self.message = "Предпросмотр: установка обновлений отключена."
            self.draw()
            return
        if self.update_busy or self.update_downloading or self.update_waiting or self.update_file is None: return
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
        self.draw()

    def unpair(self):
        if self.offline_preview:
            self.message = "Предпросмотр: отключение телефона недоступно."
            self.draw()
            return
        try:
            self.error = ""
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
            self.error = f"Не удалось отключить телефон: {exc}"
            self.message = ""
        self.draw()

    def refresh_pairing(self, redraw=True):
        if self.offline_preview:
            self.message = "Предпросмотр: QR и реальная привязка отключены."
            if redraw:
                self.draw()
            return
        try:
            self.error = ""
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
        if self.offline_preview:
            self.message = "Предпросмотр: настройки не записаны."
            self.draw()
            return
        data = {key: field.get().strip() for key, field in self.form.items()}
        data.update({"publicPort": 8765, "controlPort": 8766, "dataPort": 8767, "bridgePort": 18765})
        try:
            self.error = ""
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
