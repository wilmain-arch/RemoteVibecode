"""Lock-order regressions using an inert bridge and fake Codex RPC."""

import threading
import unittest
from unittest.mock import Mock, patch

from bridge.server import Bridge


class ObservedLock:
    """Expose when a competing thread attempts to acquire the send lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.attempted = threading.Event()
        self.owner = threading.get_ident()

    def __enter__(self):
        if threading.get_ident() != self.owner:
            self.attempted.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class FakeRpc:
    def __init__(self, active=True):
        self.proc = Mock()
        self.proc.poll.return_value = None
        self.active = active
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            turns = [{"id": "agent-turn", "status": "inProgress"}] if self.active else []
            return {"thread": {"turns": turns, "canAcceptDirectInput": True}}
        if method == "turn/steer":
            return {"turnId": "agent-turn"}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"Unexpected RPC: {method}")


class SubagentLockingTests(unittest.TestCase):
    def setUp(self):
        # Avoid Bridge.__init__: it writes state and starts background workers.
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.rpc_lock = threading.RLock()
        self.bridge.send_lock = ObservedLock()
        self.bridge.state_lock = threading.RLock()
        self.bridge.rpc = FakeRpc()
        self.bridge.update_until = 0
        self.bridge.sent_messages = {}
        self.bridge.subagent_check = Mock(return_value={"canSend": True})
        self.bridge._save_state = Mock()
        self.data = {"threadId": "root", "agentId": "agent", "action": "send",
                     "text": "Follow up", "messageId": "message-123"}
        # Fail before spawning a process if the test accidentally requests a new RPC.
        guard = patch("bridge.server.CodexRpc", side_effect=AssertionError("Real RPC forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def start_action(self, data):
        results, errors = [], []

        def run():
            try:
                results.append(self.bridge.subagent_action(data))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        return worker, results, errors

    def test_waiting_subagent_send_does_not_hold_rpc_lock(self):
        # A normal send/update holds send_lock before entering an RPC session.
        # Force the subagent to contend at exactly that point. The old ordering
        # holds rpc_lock here and prevents this owner from acquiring it.
        worker = None
        try:
            with self.bridge.send_lock:
                worker, results, errors = self.start_action(self.data)
                self.assertTrue(self.bridge.send_lock.attempted.wait(2),
                                "Subagent did not attempt send_lock")
                acquired = self.bridge.rpc_lock.acquire(timeout=1)
                if acquired:
                    self.bridge.rpc_lock.release()
                self.assertTrue(acquired, "Subagent held rpc_lock while waiting for send_lock")
        finally:
            # The context releases send_lock before joining, even on regression failure.
            if worker is not None:
                worker.join(2)
        self.assertFalse(worker.is_alive(), "Subagent send did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["turnId"], "agent-turn")
        self.assertEqual([method for method, _ in self.bridge.rpc.calls],
                         ["thread/read", "turn/steer"])

    def test_interrupt_completes_while_send_lock_is_held(self):
        with self.bridge.send_lock:
            worker, results, errors = self.start_action({**self.data, "action": "interrupt"})
            worker.join(2)
            completed = not worker.is_alive()
        worker.join(2)
        self.assertTrue(completed, "Interrupt unnecessarily waited for send_lock")
        self.assertEqual(errors, [])
        self.assertTrue(results[0]["accepted"])
        self.assertEqual([method for method, _ in self.bridge.rpc.calls],
                         ["thread/read", "turn/interrupt"])

    def test_inactive_subagent_falls_back_after_releasing_both_locks(self):
        self.bridge.rpc.active = False

        def send(text, files, message_id, agent_id):
            acquired = []

            def probe():
                for lock in (self.bridge.send_lock.lock, self.bridge.rpc_lock):
                    free = lock.acquire(blocking=False)
                    acquired.append(free)
                    if free:
                        lock.release()

            worker = threading.Thread(target=probe, daemon=True)
            worker.start()
            worker.join(2)
            self.assertEqual(acquired, [True, True], "Fallback retained a lock")
            return {"turnId": "new-turn"}

        self.bridge.send = Mock(side_effect=send)
        result = self.bridge.subagent_action(self.data)
        self.bridge.send.assert_called_once_with("Follow up", [], "message-123", "agent")
        self.assertTrue(result["accepted"])


if __name__ == "__main__":
    unittest.main()
