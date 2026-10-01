"""Open the desktop UI with sanitized offline data, never the agent runtime.

Run with ``python -m agent.ui_preview``. Optional ``--narrow`` uses the
minimum supported window size; ``--page`` selects one of the five screens.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile

from agent import updates
from agent.gui import AgentWindow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--narrow", action="store_true", help="use the minimum window size")
    parser.add_argument("--page", choices=("Обзор", "Устройства", "Подключение", "Настройки", "Обновления"),
                        default="Обзор")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="remotevibecode-ui-preview-") as directory:
        fixture = Path(directory) / "agent.json"  # intentionally absent; never copied from production
        app = AgentWindow(fixture, offline_preview=True)
        app.root.title("RemoteVibecode — безопасный UI-предпросмотр")
        app.root.geometry("900x600" if args.narrow else "1320x820")
        app.update_info = {
            "available": True,
            "info": {"version": "0.0.0-preview"},
            "manifest": {"notes": (
                "Предпросмотр заметок релиза: только синтетический текст.\n\n"
                "Ширина окна может меняться. Список изменений переносится по ширине и доступен целиком.\n"
                "Этот fixture не подключается к серверу, не читает ключи и не создаёт QR-код."
            )},
        }
        app.update_message = "Тестовые данные. Сетевые действия отключены."
        app.update_checked = "СИНТЕТИЧЕСКИЕ ДАННЫЕ"
        app.page = args.page
        app.draw()
        app.root.after_idle(app.root.focus_force)
        app.root.mainloop()


if __name__ == "__main__":
    main()
