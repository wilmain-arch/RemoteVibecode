"""Pixel-scaled overview matching the RemoteVibecode dashboard design."""

from __future__ import annotations

from pathlib import Path
import subprocess
from functools import lru_cache
import os
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

WIDTH, HEIGHT = 1672, 941
BG = "#080e1a"
SIDE = "#0d1525"
CARD = "#10192b"
INNER = "#142039"
EDGE = "#26334c"
WHITE = "#f8f9fe"
MUTED = "#a8b9dc"
DIM = "#7385aa"
GREEN = "#14dca4"
BLUE = "#536dff"
CYAN = "#00d3e9"


def _shell(image: Image.Image, draw: ImageDraw.ImageDraw, page: str, icon_path: Path) -> None:
    draw.rounded_rectangle((22, 29, 319, 914), radius=23, fill=SIDE, outline=EDGE)
    with Image.open(icon_path) as source:
        logo = source.convert("RGBA").resize((76, 76), Image.Resampling.LANCZOS)
    image.paste(logo, (43, 70), logo)
    draw.text((138, 94), "RemoteVibecode", font=_font(18, True), fill=WHITE)
    for index, (name, symbol) in enumerate((("Обзор", "home"), ("Устройства", "phone"),
                                             ("Подключение", "link"), ("Настройки", "gear"))):
        top = 183 + index * 72
        selected = name == page
        if selected:
            draw.rounded_rectangle((32, top, 308, top + 62), radius=15,
                                   fill="#17264a", outline="#1b315c")
            draw.rounded_rectangle((32, top, 37, top + 62), radius=2, fill=BLUE)
        icon_x, icon_y = 57, 199 + index * 72
        color = "#81adff" if selected else "#9cb2e4"
        if symbol == "home":
            draw.line([(icon_x, icon_y + 12), (icon_x + 14, icon_y), (icon_x + 28, icon_y + 12)],
                      fill=color, width=3, joint="curve")
            draw.line([(icon_x + 4, icon_y + 10), (icon_x + 4, icon_y + 29),
                       (icon_x + 24, icon_y + 29), (icon_x + 24, icon_y + 10)], fill=color, width=3)
            draw.rectangle((icon_x + 11, icon_y + 19, icon_x + 17, icon_y + 29), outline=color, width=2)
        elif symbol == "phone":
            draw.rounded_rectangle((icon_x + 5, icon_y, icon_x + 24, icon_y + 31),
                                   radius=3, outline=color, width=3)
            draw.line((icon_x + 11, icon_y + 26, icon_x + 18, icon_y + 26), fill=color, width=2)
        elif symbol == "link":
            draw.arc((icon_x, icon_y + 8, icon_x + 20, icon_y + 28), 45, 310, fill=color, width=3)
            draw.arc((icon_x + 10, icon_y, icon_x + 30, icon_y + 20), 225, 130, fill=color, width=3)
            draw.line((icon_x + 11, icon_y + 19, icon_x + 20, icon_y + 10), fill=color, width=3)
        else:
            draw.ellipse((icon_x + 3, icon_y + 3, icon_x + 27, icon_y + 27), outline=color, width=3)
            draw.ellipse((icon_x + 11, icon_y + 11, icon_x + 19, icon_y + 19), outline=color, width=3)
            for dx, dy in ((15, 0), (15, 30), (0, 15), (30, 15)):
                draw.ellipse((icon_x + dx - 2, icon_y + dy - 2,
                              icon_x + dx + 2, icon_y + dy + 2), fill=color)
        draw.text((117, icon_y + 3), name, font=_font(20, selected), fill=WHITE if selected else MUTED)
    draw.text((48, 868), "Закрытие окна — в трей", font=_font(14), fill=DIM)


@lru_cache(maxsize=40)
def _font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        path = fonts / ("consola.ttf" if mono else "segoeuib.ttf" if bold else "segoeui.ttf")
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return ImageFont.load_default(size=size)
    family = "DejaVu Sans Mono" if mono else "DejaVu Sans"
    try:
        path = subprocess.check_output(
            ["fc-match", "-f", "%{file}", (family + (":style=Bold" if bold else ""))],
            text=True, timeout=2,
        ).strip()
        return ImageFont.truetype(path, size)
    except (OSError, subprocess.SubprocessError):
        fallback = "/usr/share/fonts/TTF/DejaVuSansMono.ttf" if mono else "/usr/share/fonts/TTF/DejaVuSans.ttf"
        try:
            return ImageFont.truetype(fallback, size)
        except OSError:
            return ImageFont.load_default(size=size)


def render_dashboard(*, icon_path: Path, qr_path: Path | None, configured: bool,
                     bridge_ready: bool, relay_ready: bool, paired: bool,
                     address: str, error: str = "", status: str = "") -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    background = Image.new("RGBA", (WIDTH, HEIGHT))
    halo = Image.new("RGBA", (WIDTH, HEIGHT))
    ImageDraw.Draw(halo).ellipse((550, -500, 1550, 500), fill=(23, 41, 84, 35))
    background.alpha_composite(halo.filter(ImageFilter.GaussianBlur(140)))
    image.paste(background, (0, 0), background)
    draw = ImageDraw.Draw(image)

    def panel(box, radius=22, fill=CARD, edge=EDGE):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=1)

    def txt(pos, value, size=20, color=WHITE, bold=False, mono=False):
        draw.text(pos, value, font=_font(size, bold, mono), fill=color)

    def dot(x, y, active):
        draw.ellipse((x, y, x + 16, y + 16), fill=GREEN if active else DIM)

    def logo(x, y, size):
        draw.line([(x + 8, y + 29), (x + 24, y + 40), (x + 8, y + 51)], fill=WHITE, width=7, joint="curve")
        draw.line((x + 30, y + 53, x + 48, y + 53), fill=WHITE, width=6)
        for distance, shade in ((12, "#783dff"), (20, "#3d73ff"), (28, CYAN)):
            draw.arc((x + 13, y + 40 - distance, x + 13 + 2 * distance, y + 40 + distance),
                     306, 53, fill=shade, width=6)

    def symbol(kind, x, y, color=MUTED):
        if kind == "home":
            draw.line([(x, y + 12), (x + 14, y), (x + 28, y + 12)], fill=color, width=3, joint="curve")
            draw.line([(x + 4, y + 10), (x + 4, y + 29), (x + 24, y + 29), (x + 24, y + 10)], fill=color, width=3)
            draw.rectangle((x + 11, y + 19, x + 17, y + 29), outline=color, width=2)
        elif kind == "phone":
            draw.rounded_rectangle((x + 5, y, x + 24, y + 31), radius=3, outline=color, width=3)
            draw.line((x + 11, y + 26, x + 18, y + 26), fill=color, width=2)
        elif kind == "link":
            draw.arc((x, y + 8, x + 20, y + 28), 45, 310, fill=color, width=3)
            draw.arc((x + 10, y, x + 30, y + 20), 225, 130, fill=color, width=3)
            draw.line((x + 11, y + 19, x + 20, y + 10), fill=color, width=3)
        elif kind == "gear":
            draw.ellipse((x + 3, y + 3, x + 27, y + 27), outline=color, width=3)
            draw.ellipse((x + 11, y + 11, x + 19, y + 19), outline=color, width=3)
            for dx, dy in ((15, 0), (15, 30), (0, 15), (30, 15)):
                draw.ellipse((x + dx - 2, y + dy - 2, x + dx + 2, y + dy + 2), fill=color)
        elif kind == "laptop":
            draw.rounded_rectangle((x - 8, y + 2, x + 35, y + 27), radius=3, outline=color, width=3)
            draw.line((x - 14, y + 32, x + 41, y + 32), fill=color, width=4)
        elif kind == "signal":
            draw.line([(x - 9, y + 9), (x + 2, y + 16), (x - 9, y + 23)], fill=WHITE, width=5)
            draw.line((x + 2, y + 27, x + 13, y + 27), fill=WHITE, width=4)
            for distance, shade in ((12, "#724cff"), (22, BLUE), (32, CYAN)):
                draw.arc((x - 3, y + 16 - distance, x - 3 + distance * 2, y + 16 + distance),
                         300, 60, fill=shade, width=4)

    def gradient_button(box):
        x1, y1, x2, y2 = box
        mask = Image.new("L", (x2 - x1, y2 - y1))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, x2 - x1 - 1, y2 - y1 - 1), radius=16, fill=255)
        gradient = Image.new("RGB", mask.size)
        pixels = gradient.load()
        for px in range(mask.width):
            t = px / max(mask.width - 1, 1)
            color = (int(69 * (1 - t) + 0 * t), int(45 * (1 - t) + 211 * t),
                     int(245 * (1 - t) + 233 * t))
            for py in range(mask.height):
                pixels[px, py] = color
        image.paste(gradient, (x1, y1), mask)

    # The coordinate system follows the supplied 1672 × 941 reference.
    _shell(image, draw, "Обзор", icon_path)

    ready = configured and bridge_ready and relay_ready
    txt((375, 52), "Ваш компьютер готов" if ready else
        ("Настройте подключение" if not configured else "Подключаем компьютер"), 54, WHITE, True)
    dot(378, 133, ready)
    txt((412, 125), "Codex подключён" if ready else
        ("Укажите ваш сервер" if not configured else "Проверяем соединение"), 23,
        GREEN if ready else MUTED)

    panel((347, 183, 1054, 526))
    panel((1070, 183, 1646, 526))
    panel((347, 542, 1646, 915))

    # Pairing panel.
    glow = Image.new("RGBA", (WIDTH, HEIGHT))
    ImageDraw.Draw(glow).rounded_rectangle((385, 237, 610, 462), radius=27,
                                           fill=(62, 71, 250, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(19))
    image.paste(glow, (0, 0), glow)
    panel((390, 241, 605, 456), 25, WHITE, BLUE)
    if not paired and qr_path and qr_path.is_file():
        try:
            with Image.open(qr_path) as source:
                qr = source.convert("RGB").resize((184, 184), Image.Resampling.NEAREST)
            image.paste(qr, (406, 256))
        except OSError:
            txt((418, 332), "QR недоступен", 15, "#1b2434")
    else:
        txt((409, 326), "ТЕЛЕФОН ПРИВЯЗАН" if paired else "QR после запуска", 16, "#1b2434", True)
    txt((650, 244), "Телефон подключён" if paired else "Подключить телефон", 27, WHITE, True)
    txt((650, 297), "Телефон привязан к этому ПК" if paired else "Откройте RemoteVibecode", 21, MUTED)
    if not paired:
        txt((650, 330), "на телефоне и отсканируйте код", 21, MUTED)
        txt((650, 369), "Код действует 30 минут", 17, DIM)
    gradient_button((650, 419, 1022, 477))
    txt((721, 432), "↻  Создать новый код", 21, WHITE)

    # Status panel.
    txt((1102, 214), "Статус подключения", 25, WHITE, True)
    for i, (name, caption, active) in enumerate((
        ("Codex", "Активен" if bridge_ready else "Ожидание", bridge_ready),
        ("Мост", "Активен" if relay_ready else "Ожидание", relay_ready),
        ("Сеть", "Активна" if relay_ready else "Нет связи", relay_ready),
    )):
        x = (1112, 1295, 1509)[i]
        panel((x, 275, x + 105, 378), 25, INNER)
        symbol("phone" if i == 2 else "laptop" if i == 0 else "signal", x + 38, 308,
               "#c4d7ff" if i != 1 else CYAN)
        dot(x + 12, 397, active)
        txt((x + 34, 391), name, 17, WHITE, True)
        txt((x + 34, 421), caption, 15, DIM)
    for left, right, y in ((1229, 1281, 326), (1416, 1497, 326)):
        for x in range(left, right, 13):
            draw.rectangle((x, y, x + 5, y + 4), fill=CYAN if left > 1400 else BLUE)
    panel((1098, 453, 1620, 506), 20, INNER)
    txt((1161, 466), "♢  Соединение защищено сквозным шифрованием", 16, MUTED)

    # Connected devices and address, with current bridge data instead of invented phone names.
    txt((377, 569), "Привязанные устройства", 24, WHITE, True)
    panel((1384, 564, 1619, 607), 18, INNER)
    txt((1410, 572), "+  Добавить устройство", 16, WHITE)
    panel((377, 622, 1619, 735), 21, INNER)
    panel((403, 638, 485, 718), 20, "#1c2b48")
    symbol("phone", 429, 660, WHITE)
    txt((516, 645), "Привязанный телефон" if paired else "Телефон не привязан", 21, WHITE, True)
    dot(518, 685, paired)
    txt((550, 680), "Последняя активность: сейчас" if paired else "Ожидает привязки", 17, DIM)
    panel((1387, 650, 1593, 709), 19, INNER)
    txt((1423, 665), "Отключить" if paired else "Подключить", 18, WHITE)

    panel((377, 750, 1619, 895), 23, "#111b2e")
    txt((405, 769), "Адрес подключения", 20, WHITE, True)
    txt((405, 801), "Используйте этот адрес, если нужно подключить вручную", 16, MUTED)
    panel((405, 827, 1417, 880), 13, "#0c1525")
    txt((425, 839), address if configured else "Сервер ещё не настроен", 19, WHITE, mono=True)
    panel((1427, 827, 1597, 879), 14, INNER)
    txt((1453, 842), "▢  Скопировать", 16, WHITE)
    if error:
        panel((650, 486, 1022, 515), 9, "#3b2032", "#824050")
        txt((659, 490), error[:43], 13, "#ffc0bf")
    elif status:
        txt((650, 486), status[:50], 13, DIM)
    return image


def render_page(*, page: str, configured: bool, paired: bool, relay_ready: bool,
                address: str, error: str = "", status: str = "") -> Image.Image:
    """Render the other agent pages in the overview's coordinate system and palette."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    glow = Image.new("RGBA", (WIDTH, HEIGHT))
    ImageDraw.Draw(glow).ellipse((550, -500, 1550, 500), fill=(23, 41, 84, 35))
    image.paste(glow.filter(ImageFilter.GaussianBlur(140)), (0, 0),
                glow.filter(ImageFilter.GaussianBlur(140)))
    draw = ImageDraw.Draw(image)
    _shell(image, draw, page, Path(__file__).with_name("remotevibecode.ico"))

    def panel(box, radius=22, fill=CARD):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=EDGE, width=1)

    def txt(x, y, value, size=20, color=WHITE, bold=False, mono=False):
        draw.text((x, y), value, font=_font(size, bold, mono), fill=color)

    def action(box, label, primary=False):
        panel(box, 17, BLUE if primary else INNER)
        txt(box[0] + 25, box[1] + 11, label, 18, WHITE, primary)

    subtitles = {
        "Устройства": "Управляйте привязкой телефона",
        "Подключение": "Ваш сервер и состояние канала",
        "Настройки": "Адрес сервера и локальный Codex",
    }
    txt(375, 52, page, 54, WHITE, True)
    draw.ellipse((378, 133, 394, 149), fill=GREEN if relay_ready else DIM)
    txt(412, 125, subtitles[page], 23, GREEN if relay_ready else MUTED)

    if page == "Устройства":
        panel((347, 183, 1646, 542))
        txt(381, 217, "Привязанные устройства", 27, WHITE, True)
        panel((377, 280, 1619, 427), 21, INNER)
        panel((403, 309, 492, 399), 20, "#1c2b48")
        draw.rounded_rectangle((433, 328, 463, 378), radius=5, outline=WHITE, width=3)
        txt(525, 307, "Привязанный телефон" if paired else "Телефон не привязан", 23, WHITE, True)
        draw.ellipse((527, 358, 543, 374), fill=GREEN if paired else DIM)
        txt(559, 351, "Привязка активна" if paired else "Ожидает привязки", 18, MUTED)
        action((1368, 320, 1591, 388), "Отключить" if paired else "Подключить")
        txt(381, 466, "Для нового телефона откройте QR-код на вкладке «Обзор».", 18, MUTED)
        panel((347, 560, 1646, 715))
        txt(381, 594, "Как работает привязка", 24, WHITE, True)
        txt(381, 638, "Отсканируйте код в приложении RemoteVibecode. Код действует 30 минут.", 18, MUTED)
        if status or error:
            txt(381, 748, (error or status)[:100], 18, "#ffc0bf" if error else GREEN)
    elif page == "Подключение":
        panel((347, 183, 1054, 526))
        panel((1070, 183, 1646, 526))
        txt(381, 218, "Адрес сервера", 25, WHITE, True)
        panel((378, 280, 1022, 352), 15, INNER)
        txt(405, 299, address if configured else "Сервер ещё не настроен", 22, WHITE, mono=configured)
        action((378, 415, 606, 478), "Скопировать")
        txt(1102, 218, "Состояние", 25, WHITE, True)
        draw.ellipse((1104, 286, 1124, 306), fill=GREEN if relay_ready else DIM)
        txt(1144, 277, "Сервер доступен" if relay_ready else "Нет связи с сервером", 21, WHITE, True)
        txt(1102, 342, "Канал управления ПК → сервер", 17, MUTED)
        txt(1102, 384, "Входящий порт на ПК не нужен", 17, MUTED)
        panel((347, 542, 1646, 724))
        txt(381, 575, "Маршрут подключения", 24, WHITE, True)
        txt(381, 630, "Телефон   →   ваш сервер   →   агент на ПК   →   локальный Codex", 23, MUTED)
        if error:
            panel((347, 745, 1646, 830), 16, "#3b2032")
            txt(376, 769, error[:95], 18, "#ffc0bf")
        elif status:
            txt(381, 754, status[:95], 18, GREEN)
    else:
        panel((347, 183, 1646, 872))
        txt(381, 214, "Параметры подключения", 26, WHITE, True)
        txt(381, 253, "Данные хранятся на этом компьютере.", 17, MUTED)
        for index, (label, hint) in enumerate((
            ("Адрес вашего сервера", "Домен или IPv4 без порта"),
            ("SHA-256 отпечаток сертификата", "64 шестнадцатеричных символа"),
            ("Секрет сервера", "Из /etc/remotevibecode/relay-secret"),
            ("Исполняемый файл Codex", "Пусто — Codex Desktop, затем CLI"),
        )):
            y = 308 + index * 114
            txt(381, y, label, 18, WHITE, True)
            panel((378, y + 33, 1618, y + 83), 11, "#0c1525")
            txt(382, y + 87, hint, 14, DIM)
        action((378, 778, 624, 837), "Сохранить", True)
        if error or status:
            txt(650, 793, (error or status)[:74], 16, "#ffc0bf" if error else GREEN)
    return image
