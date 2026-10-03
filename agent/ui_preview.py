"""Safe offline Qt preview; synthetic data, no bridge/config/credentials."""

from pathlib import Path
import argparse, tempfile
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from agent.gui import AgentWindow, PAGES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", choices=PAGES, default="Обзор")
    parser.add_argument("--theme", choices=["light", "dark", "system"], default="light")
    parser.add_argument("--narrow", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument(
        "--state",
        choices=["ready", "empty", "pairing", "error", "download"],
        default="ready",
    )
    args = parser.parse_args(argv)
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        window = AgentWindow(Path(directory) / "agent.json", offline_preview=True)
        c = window.controller
        c.bridge_ready = True
        c.relay_ready = args.state != "error"
        c.paired = lambda: args.state not in ["empty", "pairing"]
        if args.state == "pairing":
            import qrcode

            path = Path(directory) / "demo.png"
            qrcode.make("remotevibecode://DEMO-NOT-A-REAL-PAIRING").save(path)
            c.pairing_path = str(path)
        if args.state == "error":
            c.error = "Нет связи с сервером. Агент повторяет попытку автоматически."
        c.update_info = {
            "available": args.state == "download",
            "info": {"version": "0.0.0-preview"},
            "manifest": {
                "notes": "# Обновление интерфейса\n\n- Единый дизайн «Фокус» на телефоне и компьютере.\n- Светлая, тёмная и системная темы.\n- Улучшено масштабирование и управление с клавиатуры."
            },
        }
        c.update_message = (
            "Скачано 64%"
            if args.state == "download"
            else "Установлена актуальная версия"
        )
        c.update_checked = "СИНТЕТИЧЕСКИЕ ДАННЫЕ"
        if args.state == "download":
            c.update_downloading = True
            c.update_progress = 64
        window.choose_theme(args.theme)
        window.switch(args.page)
        if args.narrow:
            window.resize(800, 600)
        window.setFocus()
        window.setWindowTitle("RemoteVibecode — безопасный Qt-предпросмотр")
        window.show()
        if args.screenshot:

            def capture():
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
                app.quit()

            QTimer.singleShot(500, capture)
        app.exec()


if __name__ == "__main__":
    main()
