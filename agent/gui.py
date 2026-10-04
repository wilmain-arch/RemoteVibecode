"""Cross-platform Qt desktop control panel in the APK Focus visual system."""

from __future__ import annotations
import json, os, sys, webbrowser
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, Signal, QUrl
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QPalette,
    QColor,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QScrollArea,
    QLineEdit,
    QTextBrowser,
    QProgressBar,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
    QButtonGroup,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QPainter, QTextCursor, QTextCharFormat, QFont, QTextFormat
from agent.controller import AgentController
from agent.qt_theme import LIGHT, DARK, stylesheet
from agent import updates

PAGES = ["Обзор", "Устройства", "Подключение", "Настройки", "Обновления"]
PATHS = {
    "home": "M3 10 12 3l9 7 M5 9v12h5v-7h4v7h5V9",
    "phone": "M7 2h10v20H7z M11 19h2",
    "link": "M9 15l6-6 M8 16l-2 2a4 4 0 0 1-6-6l5-5 M16 8l2-2a4 4 0 0 1 6 6l-5 5",
    "settings": "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
    "updates": "M20 7a9 9 0 1 0 1 8 M20 2v6h-6",
    "copy": "M9 8h11v13H9z M5 16H3V3h11v2",
    "shield": "M12 2 21 6v7q-2 6-9 9q-7-3-9-9V6z M8 12l3 3 5-6",
}


def vector_icon(name, color, size=24):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{PATHS[name]}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def label(text="", role=None):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if role:
        w.setProperty("role", role)
    return w


def vbox(parent=None, spacing=16, margins=(0, 0, 0, 0)):
    box = QVBoxLayout(parent)
    box.setSpacing(spacing)
    box.setContentsMargins(*margins)
    return box


class FocusButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.isEnabled():
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


def button(text, callback, role=None):
    w = FocusButton(text)
    w.setAccessibleName(text)
    w.clicked.connect(callback)
    if role:
        w.setProperty("role", role)
    return w


class StatusRow(QWidget):
    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 8, 0, 8)
        row.setSpacing(12)
        self.dot = label("●")
        self.dot.setFixedWidth(12)
        body = QWidget()
        col = vbox(body, 4)
        self.title = label()
        self.title.setStyleSheet("font-weight:bold")
        self.detail = label("", "muted")
        col.addWidget(self.title)
        col.addWidget(self.detail)
        row.addWidget(self.dot)
        row.addWidget(body, 1)

    def set_status(self, title, detail, active, colors):
        self.title.setText(title)
        self.detail.setText(detail)
        self.dot.setStyleSheet("color:" + colors["accent" if active else "muted"])


class SafeNotesBrowser(QTextBrowser):
    def loadResource(self, kind, url):
        # Release notes are text; never read files or fetch images from Markdown.
        return None


class AgentWindow(QMainWindow):
    def __init__(self, config_path: Path, *, offline_preview=False):
        super().__init__()
        self.offline_preview = offline_preview
        self._exiting = False
        self.controller = AgentController(config_path, offline_preview=offline_preview)
        self.controller.changed.connect(self.refresh)
        self.controller.close_requested.connect(self.exit_agent)
        self.ui_path = config_path.parent / "ui.json"
        self.preferences = {}
        if not offline_preview:
            try:
                data = json.loads(self.ui_path.read_text())
                if isinstance(data, dict):
                    self.preferences = data
            except (OSError, ValueError):
                pass
        self.theme = self.preferences.get("theme", "system")
        if self.theme not in ("system", "light", "dark"):
            self.theme = "system"
        self.page = self.preferences.get("page", "Обзор")
        if self.page not in PAGES:
            self.page = "Обзор"
        if not self.controller.configured and not self.preferences.get("page"):
            self.page = "Настройки"
        self.form = {}
        self.nav = {}
        self.icon_widgets = []
        self.cached_pair_path = None
        self.setWindowTitle("RemoteVibecode")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("remotevibecode.ico"))))
        self.resize(1280, 800)
        self.setMinimumSize(800, 600)
        width = self.preferences.get("width", 1280)
        height = self.preferences.get("height", 800)
        if isinstance(width, int) and isinstance(height, int):
            self.resize(max(800, min(width, 3000)), max(600, min(height, 2000)))
        self.build_shell()
        self.apply_theme()
        self.build_pages()
        self.switch(self.page)
        self.tray = None
        self.controller.close_requested.connect(QApplication.instance().quit)
        if not offline_preview:
            self.start_tray()
            self.controller.start_agent()
            QTimer.singleShot(3000, lambda: self.controller.check_update(manual=False))
            self.auto_timer = QTimer(self)
            self.auto_timer.timeout.connect(
                lambda: self.controller.check_update(manual=False)
            )
            self.auto_timer.start(3_600_000)
            self.state_timer = QTimer(self)
            self.state_timer.timeout.connect(self.controller.refresh_state)
            self.state_timer.start(1000)
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            lambda _: self.apply_theme()
        )
        self.refresh()

    def build_shell(self):
        shell = QWidget()
        self.setCentralWidget(shell)
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(224)
        sb = vbox(self.sidebar, 4, (16, 28, 16, 24))
        brand = QHBoxLayout()
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(self.windowIcon().pixmap(32, 32))
        self.brand_text = label("RemoteVibecode")
        self.brand_text.setStyleSheet("font-weight:bold")
        brand.addWidget(self.brand_icon)
        brand.addWidget(self.brand_text)
        sb.addLayout(brand)
        sb.addSpacing(28)
        for i, page in enumerate(PAGES):
            b = button(page, lambda checked=False, p=page: self.switch(p), "nav")
            b.setCheckable(True)
            b.setIconSize(QSize(22, 22))
            b.setToolTip(page)
            self.nav[page] = b
            sb.addWidget(b)
            shortcut = QShortcut(QKeySequence(f"Alt+{i + 1}"), self)
            shortcut.activated.connect(lambda p=page: self.switch(p))
        sb.addStretch()
        self.footer = label(
            "Закрытие окна — в трей\nRemoteVibecode · " + updates.VERSION, "muted"
        )
        sb.addWidget(self.footer)
        row.addWidget(self.sidebar)
        content = QWidget()
        col = vbox(content, 16, (40, 36, 40, 24))
        self.title = label("", "title")
        self.subtitle = label("", "muted")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        self.notice = label()
        self.notice.setAccessibleName("Состояние операции")
        self.notice.hide()
        col.addWidget(self.notice)
        content.setMaximumWidth(1112)
        holder = QWidget()
        center = QHBoxLayout(holder)
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch()
        center.addWidget(content, 1)
        center.addStretch()
        row.addWidget(holder, 1)

    def new_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = vbox(body, 16, (0, 16, 8, 16))
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        return scroll, body, layout

    def section(self, layout, title):
        layout.addSpacing(12)
        layout.addWidget(label(title, "section"))

    def icon_label(self, name, size=24):
        w = QLabel()
        w.setFixedSize(size, size)
        self.icon_widgets.append((w, name, size))
        return w

    def device_row(self, layout):
        w = QWidget()
        r = QHBoxLayout(w)
        r.setContentsMargins(0, 8, 0, 8)
        r.setSpacing(16)
        r.addWidget(self.icon_label("phone"))
        text = QWidget()
        c = vbox(text, 4)
        title = label()
        title.setStyleSheet("font-weight:bold")
        detail = label("", "muted")
        c.addWidget(title)
        c.addWidget(detail)
        r.addWidget(text, 1)
        action = button("", self.device_action)
        r.addWidget(action)
        layout.addWidget(w)
        return title, detail, action

    def build_pages(self):
        self.overview_scroll, _, o = self.new_page()
        self.overview_top = QWidget()
        self.overview_grid = QHBoxLayout(self.overview_top)
        self.overview_grid.setContentsMargins(0, 0, 0, 0)
        self.overview_grid.setSpacing(40)
        hero = QFrame()
        hero.setProperty("role", "hero")
        hr = QHBoxLayout(hero)
        hr.setContentsMargins(28, 28, 28, 28)
        hr.setSpacing(24)
        self.hero_image = QLabel()
        self.hero_image.setFixedSize(80, 160)
        self.hero_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hr.addWidget(self.hero_image)
        ht = QWidget()
        hc = vbox(ht, 12)
        self.hero_title = label("", "heroTitle")
        self.hero_detail = label("", "muted")
        self.hero_hint = label("", "muted")
        self.hero_action = button("", self.show_pairing)
        hc.addWidget(self.hero_title)
        hc.addWidget(self.hero_detail)
        hc.addWidget(self.hero_hint)
        hc.addWidget(self.hero_action, 0, Qt.AlignmentFlag.AlignLeft)
        hr.addWidget(ht, 1)
        self.overview_grid.addWidget(hero, 6)
        states = QWidget()
        st = vbox(states, 12)
        st.addWidget(label("Соединение", "section"))
        self.status_codex = StatusRow()
        self.status_relay = StatusRow()
        st.addWidget(self.status_codex)
        st.addWidget(self.status_relay)
        security = QHBoxLayout()
        security.addWidget(self.icon_label("shield", 18))
        security.addWidget(label("Сквозное шифрование", "muted"))
        st.addLayout(security)
        st.addStretch()
        self.overview_grid.addWidget(states, 5)
        o.addWidget(self.overview_top)
        self.section(o, "Устройство")
        self.overview_device = self.device_row(o)
        self.section(o, "Адрес подключения")
        o.addWidget(label("Для ручной настройки телефона.", "muted"))
        self.overview_address = self.address_row(o)
        o.addStretch()
        self.devices_scroll, _, d = self.new_page()
        self.devices_row = self.device_row(d)
        self.section(d, "Подключить телефон")
        d.addWidget(
            label(
                "Новая привязка заменит текущую. Перед заменой приложение запросит подтверждение.",
                "muted",
            )
        )
        self.device_pair_button = button(
            "Показать QR-код", self.show_pairing, "primary"
        )
        d.addWidget(self.device_pair_button, 0, Qt.AlignmentFlag.AlignLeft)
        d.addWidget(label("Код действует 30 минут", "muted"))
        d.addStretch()
        self.connection_scroll, _, c = self.new_page()
        self.section(c, "Адрес сервера")
        self.connection_address = self.address_row(c)
        self.section(c, "Состояние канала")
        self.connection_codex = StatusRow()
        self.connection_relay = StatusRow()
        c.addWidget(self.connection_codex)
        c.addWidget(self.connection_relay)
        self.section(c, "Маршрут подключения")
        c.addWidget(label("Телефон → ваш сервер → агент на ПК → Codex"))
        c.addWidget(label("Входящий порт на компьютере не требуется.", "muted"))
        c.addStretch()
        # Settings use a dedicated scrollable form and a permanently reachable save action.
        settings = QWidget()
        sl = vbox(settings, 16)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        form_body = QWidget()
        form_body.setMaximumWidth(700)
        f = vbox(form_body, 12, (0, 16, 8, 16))
        sc.setWidget(form_body)
        sl.addWidget(sc, 1)
        self.section(f, "Оформление")
        segmented = QFrame()
        segmented.setProperty("role", "segmented")
        seg = QHBoxLayout(segmented)
        seg.setContentsMargins(4, 4, 4, 4)
        seg.setSpacing(4)
        self.theme_buttons = {}
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        for key, title in [
            ("system", "Система"),
            ("light", "Светлая"),
            ("dark", "Тёмная"),
        ]:
            b = button(title, lambda checked=False, k=key: self.choose_theme(k))
            b.setCheckable(True)
            self.theme_buttons[key] = b
            self.theme_group.addButton(b)
            seg.addWidget(b)
        segment_row = QHBoxLayout()
        segment_row.addWidget(segmented)
        segment_row.addStretch()
        f.addLayout(segment_row)
        self.section(f, "Сервер")
        for key, title, hint in [
            ("relayHost", "Адрес сервера", "Домен или IPv4 без порта"),
            (
                "relayFingerprint",
                "SHA-256 отпечаток сертификата",
                "64 шестнадцатеричных символа из настроек ретранслятора",
            ),
            ("relaySecret", "Секрет сервера", "Хранится только на этом компьютере"),
            (
                "codexExecutable",
                "Исполняемый файл Codex",
                "Пусто — сначала Codex Desktop, затем CLI",
            ),
        ]:
            if key == "codexExecutable":
                self.section(f, "Codex")
            text = label(title)
            entry = QLineEdit(str(self.controller.config.get(key, "")))
            entry.setAccessibleName(title)
            text.setBuddy(entry)
            f.addWidget(text)
            if key == "relaySecret":
                entry.setEchoMode(QLineEdit.EchoMode.Password)
                rr = QHBoxLayout()
                rr.addWidget(entry, 1)
                self.secret_toggle = button("Показать", self.toggle_secret)
                rr.addWidget(self.secret_toggle)
                f.addLayout(rr)
            else:
                f.addWidget(entry)
            if key == "codexExecutable":
                entry.setPlaceholderText("Автоматический поиск")
            entry.textEdited.connect(lambda _: self.clear_field_errors())
            f.addWidget(label(hint, "muted"))
            self.form[key] = entry
            err = label("", "error")
            err.hide()
            f.addWidget(err)
            entry.error_label = err
        f.addStretch()
        self.save_button = button("Сохранить настройки", self.save_settings, "primary")
        sl.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(settings)
        self.updates_scroll, _, u = self.new_page()
        self.update_title = label("", "section")
        self.update_status = label("", "muted")
        self.update_checked = label("", "muted")
        u.addWidget(self.update_title)
        u.addWidget(self.update_status)
        u.addWidget(self.update_checked)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Скачивание обновления")
        u.addWidget(self.progress)
        actions = QHBoxLayout()
        self.check_button = button(
            "Проверить обновления", self.controller.check_update, "primary"
        )
        self.update_action = button("", self.perform_update, "primary")
        actions.addWidget(self.check_button)
        actions.addWidget(self.update_action)
        actions.addStretch()
        u.addLayout(actions)
        self.section(u, "Что изменилось")
        self.notes = SafeNotesBrowser()
        self.notes.setAccessibleName("Заметки релиза")
        self.notes.setOpenLinks(False)
        self.notes.anchorClicked.connect(self.open_note_link)
        self.notes.document().setDefaultStyleSheet(
            "h1,h2,h3 { font-size:18px; } p,li { line-height:150%; margin-top:8px; margin-bottom:8px; }"
        )
        self.notes.document().documentLayout().documentSizeChanged.connect(
            self.resize_notes
        )
        u.addWidget(self.notes)
        u.addWidget(
            button("Открыть релиз на GitHub", self.open_release),
            0,
            Qt.AlignmentFlag.AlignLeft,
        )
        u.addWidget(
            label(
                "Установка дождётся завершения задач и очереди.\nНастройки и привязка телефона сохранятся.",
                "muted",
            )
        )
        u.addStretch()

    def resize_notes(self, size):
        self.notes.setFixedHeight(min(400, max(100, int(size.height()) + 16)))

    def address_row(self, layout):
        row = QHBoxLayout()
        w = QLineEdit()
        w.setReadOnly(True)
        w.setAccessibleName("Адрес подключения")
        row.addWidget(w, 1)
        b = button("Копировать", lambda: self.copy_address(w.text()))
        b.setIcon(vector_icon("copy", self.colors["accent"]))
        row.addWidget(b)
        layout.addLayout(row)
        return w

    def choose_theme(self, key):
        self.theme = key
        self.preferences["theme"] = key
        self.apply_theme()
        self.save_preferences()

    def apply_theme(self):
        dark = self.theme == "dark" or (
            self.theme == "system"
            and QApplication.instance().styleHints().colorScheme()
            == Qt.ColorScheme.Dark
        )
        self.colors = DARK if dark else LIGHT
        self.setStyleSheet(stylesheet(self.colors))
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(self.colors["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(self.colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(self.colors["bg"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(self.colors["text"]))
        self.setPalette(palette)
        for p, name in zip(PAGES, ["home", "phone", "link", "settings", "updates"]):
            self.nav[p].setIcon(
                vector_icon(
                    name,
                    self.colors["text"] if self.page == p else self.colors["muted"],
                )
            )
        for w, name, size in self.icon_widgets:
            w.setPixmap(
                vector_icon(name, self.colors["accent"], size).pixmap(size, size)
            )
        if hasattr(self, "theme_buttons"):
            for key, b in self.theme_buttons.items():
                b.setChecked(key == self.theme)
                b.setStyleSheet(
                    f"background:{self.colors['bg'] if key == self.theme else 'transparent'};"
                )
        if hasattr(self, "hero_image"):
            self.refresh()

    def switch(self, page):
        self.page = page
        self.stack.setCurrentIndex(PAGES.index(page))
        for p, b in self.nav.items():
            b.setChecked(p == page)
        self.apply_theme()
        self.refresh()
        self.save_preferences()

    def refresh(self):
        c = self.controller
        paired = c.paired()
        self.title.setText(
            ("Ваш компьютер готов" if c.bridge_ready and c.codex_ready and c.relay_ready else "Обзор")
            if self.page == "Обзор"
            else self.page
        )
        subtitles = {
            "Обзор": "● Codex доступен"
            if c.codex_ready
            else "Проверяем соединение с Codex и сервером.",
            "Устройства": "Привязка телефона к этому компьютеру.",
            "Подключение": "Ваш сервер и состояние защищённого канала.",
            "Настройки": "Оформление и параметры этого компьютера.",
            "Обновления": "RemoteVibecode · текущая версия " + updates.VERSION,
        }
        if self.page == "Настройки" and not c.configured:
            self.title.setText("Подключим ваш компьютер")
            subtitles["Настройки"] = "Укажите сервер, затем подключите телефон."
        self.save_button.setText(
            "Сохранить настройки" if c.configured else "Подключить компьютер"
        )
        self.subtitle.setText(subtitles[self.page])
        self.subtitle.setProperty(
            "role", "accent" if self.page == "Обзор" and c.bridge_ready else "muted"
        )
        self.subtitle.style().unpolish(self.subtitle)
        self.subtitle.style().polish(self.subtitle)
        self.hero_title.setText("Телефон привязан" if paired else "Подключить телефон")
        self.hero_detail.setText(
            "Можно продолжать работу в приложении RemoteVibecode."
            if paired
            else "Откройте приложение на телефоне и отсканируйте QR-код."
        )
        self.hero_hint.setText("" if paired else "Код действует 30 минут")
        self.hero_action.setText(
            "Открыть устройства"
            if paired
            else ("Настроить подключение" if not c.configured else "Показать QR-код")
        )
        self.hero_action.setAccessibleName(self.hero_action.text())
        self.save_button.setAccessibleName(self.save_button.text())
        self.hero_action.setEnabled(not c.operation_busy)
        path = c.pairing_path if not paired else ""
        self.hero_image.setFixedWidth(160 if path else 80)
        if path != self.cached_pair_path:
            self.cached_pair_path = path
            pix = QPixmap(path) if path else QPixmap()
            self.hero_image.setPixmap(
                pix.scaled(
                    160,
                    160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                if not pix.isNull()
                else vector_icon("phone", self.colors["accent"], 48).pixmap(48, 48)
            )
        elif not path:
            self.hero_image.setPixmap(
                vector_icon("phone", self.colors["accent"], 48).pixmap(48, 48)
            )
        codex = "● Codex на этом ПК\n   " + (
            "Активен" if c.bridge_ready else "Ожидание"
        )
        relay = "● Ретранслятор\n   " + (
            "Доступен" if c.relay_ready else "Повторное подключение…"
        )
        for w in [self.status_codex, self.connection_codex]:
            w.set_status(
                "Codex на этом ПК",
                "Доступен" if c.codex_ready else "Не подтверждён",
                c.codex_ready,
                self.colors,
            )
        for w in [self.status_relay, self.connection_relay]:
            w.set_status(
                "Ретранслятор",
                "Доступен" if c.relay_ready else "Повторное подключение…",
                c.relay_ready,
                self.colors,
            )
        for title, detail, action in [self.overview_device, self.devices_row]:
            title.setText(
                "Привязанный телефон" if paired else "Телефон ещё не привязан"
            )
            detail.setText(
                "Привязан к этому компьютеру"
                if paired
                else "Отсканируйте QR-код в приложении"
            )
            action.setText("Отключить" if paired else "Подключить телефон")
            action.setAccessibleName(action.text())
            action.setEnabled(
                not c.operation_busy and (not c.configured or c.bridge_ready)
            )
        self.device_pair_button.setEnabled(
            not c.operation_busy and (not c.configured or c.bridge_ready)
        )
        address = (
            f"https://{c.active_config.get('relayHost', '')}:{c.active_config.get('publicPort', 8765)}"
            if c.configured
            else "Сервер ещё не настроен"
        )
        for w in [self.overview_address, self.connection_address]:
            if w.text() != address:
                w.setText(address)
        self.notice.setText(c.error or c.message or c.update_error)
        self.notice.setProperty(
            "role", "error" if c.error or c.update_error else "muted"
        )
        self.notice.setVisible(bool(self.notice.text()))
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        info = c.update_info or {}
        available = info.get("available", False)
        self.update_title.setText(
            "Доступна версия " + str(info.get("info", {}).get("version", ""))
            if available
            else ("Установлена актуальная версия" if info else "Проверка обновлений")
        )
        self.update_status.setText(
            c.update_message or "Проверяем автоматически раз в сутки."
        )
        self.update_status.setVisible(
            self.update_status.text() != self.update_title.text()
        )
        self.update_checked.setText(
            "Проверено: " + c.update_checked if c.update_checked else ""
        )
        self.progress.setVisible(c.update_downloading)
        self.progress.setValue(int(c.update_progress))
        self.check_button.setEnabled(
            not (c.update_busy or c.update_downloading or c.update_waiting)
        )
        self.update_action.setVisible(bool(available))
        self.update_action.setText(
            "Отменить"
            if c.update_downloading or c.update_waiting
            else "Установить"
            if c.update_file
            else "Скачать обновление"
        )
        self.update_action.setAccessibleName(self.update_action.text())
        self.update_action.setEnabled(not c.update_busy)
        notes = (
            info.get("manifest", {}).get("notes", "")
            or "Скачивание не прерывает работу чата.\n\nУстановка ждёт завершения задач и очереди. Настройки и привязка телефона сохраняются."
        )
        if (
            self.notes.toMarkdown().strip() != notes.strip()
            and getattr(self, "_notes_source", None) != notes
        ):
            self._notes_source = notes
            self.notes.setMarkdown(notes)
            block = self.notes.document().begin()
            while block.isValid():
                if block.blockFormat().headingLevel():
                    cursor = QTextCursor(block)
                    cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    font = QFont()
                    font.setPixelSize(18)
                    font.setWeight(QFont.Weight.DemiBold)
                    fmt = QTextCharFormat()
                    fmt.setFont(font)
                    fmt.setFontPointSize(13.5)
                    fmt.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
                    cursor.mergeCharFormat(fmt)
                block = block.next()

    def clear_field_errors(self):
        for e in self.form.values():
            e.setProperty("invalid", False)
            e.error_label.hide()
            e.style().unpolish(e)
            e.style().polish(e)

    def save_settings(self):
        self.clear_field_errors()
        values = {k: e.text() for k, e in self.form.items()}
        errors = {}
        host = values["relayHost"].strip()
        if not host or "/" in host or ":" in host:
            errors["relayHost"] = "Укажите домен или IPv4 без протокола и порта."
        import re

        if not re.fullmatch(
            r"[0-9a-fA-F]{64}", values["relayFingerprint"].replace(":", "").strip()
        ):
            errors["relayFingerprint"] = (
                "Нужен SHA-256 отпечаток: 64 шестнадцатеричных символа."
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", values["relaySecret"].strip()):
            errors["relaySecret"] = (
                "Секрет должен содержать 64 шестнадцатеричных символа."
            )
        if errors and not self.offline_preview:
            for key, text in errors.items():
                e = self.form[key]
                e.setProperty("invalid", True)
                e.error_label.setText(text)
                e.error_label.show()
                e.style().unpolish(e)
                e.style().polish(e)
            self.form[next(iter(errors))].setFocus()
            return
        self.controller.save_settings(values)

    def toggle_secret(self):
        entry = self.form["relaySecret"]
        hidden = entry.echoMode() == QLineEdit.EchoMode.Password
        entry.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
        )
        self.secret_toggle.setText("Скрыть" if hidden else "Показать")
        self.secret_toggle.setAccessibleName(self.secret_toggle.text())

    def show_pairing(self):
        c = self.controller
        if not c.configured:
            self.switch("Настройки")
            return
        if c.paired() and self.page == "Обзор":
            self.switch("Устройства")
            return
        if c.paired() and not self.confirm(
            "Заменить привязку?",
            "Текущий телефон потеряет доступ. Новый QR-код позволит привязать другое устройство.",
        ):
            return
        self.switch("Обзор")
        if c.pairing_path and Path(c.pairing_path).is_file() and not c.paired():
            return
        c.pairing_request()

    def device_action(self):
        if not self.controller.paired():
            self.show_pairing()
            return
        if self.confirm(
            "Отключить телефон?",
            "Телефон потеряет доступ к этому компьютеру. Будет создан новый код привязки.",
        ):
            self.controller.pairing_request(unpair=True)

    def confirm(self, title, text):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Продолжить")
        box.button(QMessageBox.StandardButton.Cancel).setText("Отмена")
        return box.exec() == QMessageBox.StandardButton.Yes

    def copy_address(self, text):
        if not self.controller.configured:
            return
        QApplication.clipboard().setText(text)
        self.controller.message = "Адрес скопирован"
        self.refresh()

    def perform_update(self):
        c = self.controller
        if c.update_downloading or c.update_waiting:
            c.update_cancel.set()
            c.update_message = "Отменяю…"
            self.refresh()
        elif c.update_file:
            c.install_update()
        else:
            c.download_update()

    def open_note_link(self, url):
        if not self.offline_preview and url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def open_release(self):
        if not self.offline_preview:
            QDesktopServices.openUrl(
                QUrl("https://github.com/wilmain-arch/RemoteVibecode/releases/latest")
            )

    def save_preferences(self):
        if self.offline_preview:
            return
        self.preferences.update(
            theme=self.theme, page=self.page, width=self.width(), height=self.height()
        )
        try:
            self.ui_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ui_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.preferences, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, self.ui_path)
        except OSError:
            pass

    def start_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(
            QIcon(str(Path(__file__).with_name("remotevibecode-tray.ico"))), self
        )
        self.tray.setToolTip("RemoteVibecode")
        menu = QMenu(self)
        menu.addAction("Открыть", self.show_window)
        menu.addAction("Выйти", self.exit_agent)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.save_preferences()
        if self.offline_preview or self._exiting:
            event.accept()
            return
        event.ignore()
        if (
            self.tray
            and self.tray.isVisible()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self.hide()
        else:
            self.showMinimized()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            compact = self.width() < 1000
            self.sidebar.setFixedWidth(72 if compact else 224)
            self.brand_text.setVisible(not compact)
            self.footer.setVisible(not compact)
            for page, b in self.nav.items():
                b.setText("" if compact else page)
        if hasattr(self, "overview_grid"):
            self.overview_grid.setDirection(
                QHBoxLayout.Direction.TopToBottom
                if self.width() < 1150
                else QHBoxLayout.Direction.LeftToRight
            )

    def exit_agent(self):
        self._exiting = True
        self.save_preferences()
        self.controller.stop.set()
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.instance().exit(0)


def launch(config_path: Path, update_ready: Path | None = None):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = (config_path.parent / "agent.log").open(
        "a", encoding="utf-8", buffering=1
    )
    sys.stdout = sys.stderr = log_file
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setApplicationName("RemoteVibecode")
    window = AgentWindow(config_path)
    window.show()
    if update_ready:
        timer = QTimer(window)

        def ready():
            if not window.controller.configured or window.controller.bridge_ready:
                update_ready.write_text("ready")
                timer.stop()

        timer.timeout.connect(ready)
        timer.start(500)
    try:
        app.exec()
    finally:
        window.controller.stop.set()
        log_file.close()
