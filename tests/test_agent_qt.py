"""Offline Qt regression tests; no personal config, live bridge, or paid tasks."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

QT = importlib.util.find_spec("PySide6") is not None
if QT:
    from PySide6.QtWidgets import QApplication, QLineEdit
    from PySide6.QtCore import QUrl, Qt, QCoreApplication, QEvent
    from agent.gui import AgentWindow, PAGES
    from agent.controller import AgentController


@unittest.skipUnless(QT, "PySide6 is required for desktop UI tests")
class QtAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "agent.json"
        self.window = AgentWindow(self.path, offline_preview=True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.directory.cleanup()

    def test_failed_start_and_late_health_do_not_fake_readiness(self):
        controller = self.window.controller
        controller.started = controller.bridge_ready = controller.codex_ready = True
        generation = controller.health_generation
        controller.consume(("stopped",))
        self.assertFalse(controller.started)
        controller.consume(("health", {"codexReady": True}, generation))
        self.assertFalse(controller.codex_ready)
        controller.consume(("health", {"codexReady": True}, controller.health_generation))
        self.assertFalse(controller.codex_ready)

    def test_all_pages_and_themes(self):
        for theme in ["light", "dark", "system"]:
            self.window.choose_theme(theme)
            for page in PAGES:
                self.window.switch(page)
                self.app.processEvents()
                self.assertEqual(self.window.stack.currentIndex(), PAGES.index(page))
                self.assertTrue(self.window.nav[page].isChecked())

    def test_draft_focus_and_selection_survive_events(self):
        self.window.switch("Настройки")
        e = self.window.form["relayHost"]
        e.setText("draft.example.org")
        e.setFocus()
        e.setSelection(0, 5)
        self.window.controller.consume(("relay", False, "offline"))
        self.assertEqual(e.text(), "draft.example.org")
        self.assertTrue(e.hasFocus())
        self.assertEqual(e.selectedText(), "draft")
        self.window.switch("Обзор")
        self.window.switch("Настройки")
        self.window.choose_theme("dark")
        self.assertEqual(e.text(), "draft.example.org")

    def test_compact_shell_and_reachable_save(self):
        self.window.resize(800, 600)
        self.window.switch("Настройки")
        self.app.processEvents()
        self.assertEqual(self.window.sidebar.width(), 72)
        self.assertTrue(self.window.save_button.isVisible())
        self.assertLess(
            self.window.save_button.mapTo(
                self.window, self.window.save_button.rect().bottomRight()
            ).y(),
            600,
        )

    def test_no_fake_ready_title(self):
        self.window.controller.bridge_ready = True
        self.window.controller.relay_ready = False
        self.window.refresh()
        self.assertEqual(self.window.title.text(), "Обзор")

    def test_secret_toggle(self):
        e = self.window.form["relaySecret"]
        self.assertEqual(e.echoMode(), QLineEdit.EchoMode.Password)
        self.window.toggle_secret()
        self.assertEqual(e.echoMode(), QLineEdit.EchoMode.Normal)
        self.window.toggle_secret()
        self.assertEqual(e.echoMode(), QLineEdit.EchoMode.Password)

    def test_preview_never_writes_or_starts(self):
        self.window.controller.start_agent()
        self.window.controller.save_settings({})
        self.window.controller.pairing_request()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.window.controller.started)
        self.assertFalse(self.window.ui_path.exists())

    def test_dangerous_action_cancelled(self):
        self.window.controller.paired = lambda: True
        with (
            patch.object(self.window, "confirm", return_value=False),
            patch.object(self.window.controller, "pairing_request") as action,
        ):
            self.window.device_action()
            action.assert_not_called()

    def test_notes_deny_resources_and_unsafe_links(self):
        self.assertIsNone(self.window.notes.loadResource(2, QUrl("file:///etc/passwd")))
        self.window.offline_preview = False
        with patch("agent.gui.QDesktopServices.openUrl") as opened:
            self.window.open_note_link(QUrl("file:///etc/passwd"))
            opened.assert_not_called()
            self.window.open_note_link(QUrl("https://example.org"))
            opened.assert_called_once()

    def test_updates_progress_and_cancel(self):
        c = self.window.controller
        c.update_info = {"available": True, "info": {"version": "9"}, "manifest": {}}
        c.update_downloading = True
        c.consume(("update", "progress", 64))
        self.assertEqual(self.window.progress.value(), 64)
        self.assertEqual(self.window.update_action.text(), "Отменить")
        self.window.perform_update()
        self.assertTrue(c.update_cancel.is_set())

    def test_queued_worker_event(self):
        t = threading.Thread(
            target=lambda: self.window.controller.events.put(("relay", True))
        )
        t.start()
        t.join()
        self.app.processEvents()
        self.assertTrue(self.window.controller.relay_ready)

    def test_ui_preferences_have_no_secret(self):
        self.window.offline_preview = False
        self.window.form["relaySecret"].setText("SECRET-TEST")
        self.window.choose_theme("dark")
        data = json.loads(self.window.ui_path.read_text())
        self.assertEqual(data["theme"], "dark")
        self.assertNotIn("SECRET", self.window.ui_path.read_text())
        self.window.offline_preview = True

    def test_unconfigured_agent_does_not_start(self):
        c = AgentController(self.path)
        with patch("agent.controller.run_agent") as run:
            c.start_agent()
            run.assert_not_called()
        self.assertFalse(c.started)

    def test_config_save_retains_ports_and_running_connection(self):
        c = AgentController(self.path)
        c.config = {
            "relayHost": "old.example.org",
            "bridgePort": 12345,
            "publicPort": 9999,
        }
        c.started = True
        c._active_config = dict(c.config)
        with patch("agent.controller.load_config_from_dict", side_effect=lambda d: d):
            self.assertTrue(
                c.save_settings(
                    {
                        "relayHost": "new.example.org",
                        "relaySecret": "f" * 64,
                        "relayFingerprint": "a" * 64,
                        "codexExecutable": "",
                    }
                )
            )
        self.assertEqual(c.config["bridgePort"], 12345)
        self.assertEqual(c.config["publicPort"], 9999)
        self.assertEqual(c.active_config["relayHost"], "old.example.org")
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_confirmed_install_respects_cancellation(self):
        c = self.window.controller
        c.update_cancel.set()
        c.update_waiting = True
        with patch("agent.controller.updates.prepare_install") as install:
            c.consume(("update", "install", None))
            install.assert_not_called()
        self.assertFalse(c.update_waiting)

    def test_repeated_page_switch_keeps_same_controller(self):
        old = self.window.controller
        for page in PAGES:
            self.window.switch(page)
        self.assertIs(self.window.controller, old)

    def test_pairing_worker_uses_isolated_active_connection(self):
        import io

        c = self.window.controller
        c.offline_preview = False
        c.bridge_ready = True
        c.config = {"bridgePort": 12345, "relayHost": "demo.invalid"}
        (self.path.parent / "state.json").write_text(
            json.dumps({"token": "SYNTHETIC-TOKEN"})
        )

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with (
            patch("agent.controller.threading.Thread", ImmediateThread),
            patch(
                "agent.controller.request.urlopen",
                return_value=io.BytesIO(b'{"pin":"000000"}'),
            ) as post,
            patch(
                "agent.controller.ensure_certificate", return_value=(None, None, "DEMO")
            ),
            patch(
                "agent.controller.pairing_image",
                return_value=self.path.parent / "demo.png",
            ),
        ):
            c.pairing_request()
            self.app.processEvents()
            self.assertFalse(c.operation_busy)
            self.assertEqual(c.pairing_path, str(self.path.parent / "demo.png"))
            self.assertEqual(
                post.call_args.args[0].full_url,
                "https://127.0.0.1:12345/api/pairing/rotate",
            )
            self.assertEqual(post.call_args.kwargs["timeout"], 10)

    def test_network_operation_failure_releases_busy_state(self):
        c = self.window.controller
        c.offline_preview = False
        c.bridge_ready = True

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("agent.controller.threading.Thread", ImmediateThread):
            c.pairing_request()
            self.app.processEvents()
        self.assertFalse(c.operation_busy)
        self.assertIn("Не удалось", c.error)

    def test_close_without_tray_minimizes_keeps_agent(self):
        from PySide6.QtGui import QCloseEvent

        self.window.offline_preview = False
        self.window.tray = None
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.isMinimized())
        self.assertFalse(self.window.controller.stop.is_set())
        self.window.offline_preview = True

    def test_explicit_exit_accepts_close(self):
        from PySide6.QtGui import QCloseEvent

        self.window.offline_preview = False
        self.window._exiting = True
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.window.offline_preview = True

    def test_enter_activates_focused_navigation(self):
        from PySide6.QtTest import QTest

        self.window.nav["Настройки"].setFocus()
        QTest.keyClick(self.window.nav["Настройки"], Qt.Key.Key_Return)
        self.assertEqual(self.window.page, "Настройки")


if __name__ == "__main__":
    unittest.main()
