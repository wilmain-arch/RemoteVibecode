"""Regression contracts for F01/F02/F04/F05/F06/F07/F08/F09/F11/F18.
All tasks are inert and all files/servers are isolated.
"""
import io
import json
import queue
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock
from bridge.server import Bridge, BusyError, CodexRpc
from bridge.controls import Controls


def inert(root, rpc):
    b = Bridge.__new__(Bridge)
    b.thread_id = b.selected_thread_id = 'draft-test'
    b.draft_threads = {'draft-test': str(root)}
    b.state_lock = threading.RLock(); b.send_lock = threading.Lock()
    b.submission_lock = threading.Lock(); b.turn_lock = threading.Lock(); b.queue_lock = threading.Lock()
    b.upload_lock = threading.Lock(); b.upload_dir = root
    b.active_turns = {}; b.pending = {}; b.sent_messages = {}; b.cancelled_sends = {}; b.dismissed_sends = {}; b.queued_sends = {}
    b.send_operations = {}; b.project_overrides = {}; b.thread_modes = {}; b.task_usage = {}; b.update_until = 0
    b.last_queue_error = ''; b.controls = Controls(b)
    b.chat_images_lock = threading.Lock(); b.chat_files = {}
    b._save_state = lambda: None; b.usage_limits = lambda: None
    @contextmanager
    def session(): yield rpc
    b.rpc_session = session
    return b


class Rpc:
    def __init__(self):
        self.created = []; self.starts = []; self.turns = []; self.native = []; self.adds = 0
        self.lose_turn = False; self.lose_thread = False; self.lose_queue = False
    def call(self, method, params, **kwargs):
        if method == 'thread/start':
            self.created.append('new-' + str(len(self.created) + 1))
            if self.lose_thread: raise TimeoutError('Lost thread reply')
            return {'thread': {'id': self.created[-1]}}
        if method == 'turn/start':
            self.starts.append(params)
            self.turns.append({'id': 'accepted', 'items': [{'type': 'userMessage', 'clientId': params['clientUserMessageId']}]})
            if self.lose_turn: raise TimeoutError('Lost turn reply')
            return {'turn': {'id': 'accepted'}}
        if method == 'thread/read': return {'thread': {'turns': self.turns, 'cwd': '/synthetic'}}
        if method == 'thread/queue/list': return {'data': self.native, 'nextCursor': None}
        if method == 'thread/queue/add':
            self.adds += 1
            item = {'id': 'native', 'clientUserMessageId': params['clientUserMessageId'], 'input': params['input']}
            self.native.append(item)
            if self.lose_queue: raise TimeoutError('Lost queue reply')
            return {'queuedSubmission': item}
        return {}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.rpc = Rpc(); self.b = inert(self.root, self.rpc)
    def test_first_accepted_turn_survives_lost_reply_and_restart(self):
        self.rpc.lose_turn = True
        with self.assertRaises(TimeoutError): self.b.send('Task', [], 'stable', 'draft-test')
        restored = inert(self.root, self.rpc)
        restored.send_operations = json.loads(json.dumps(self.b.send_operations))
        result = restored.send('Task', [], 'stable', 'draft-test')
        self.assertEqual(result['turnId'], 'accepted')
        self.assertEqual(len(self.rpc.created), 1); self.assertEqual(len(self.rpc.starts), 1)
        self.assertNotIn('draft-test', restored.draft_threads)
    def test_unknown_turn_is_never_launched_again(self):
        self.rpc.lose_turn = True
        with self.assertRaises(TimeoutError): self.b.send('Task', [], 'stable', 'draft-test')
        self.rpc.turns = []
        with self.assertRaisesRegex(RuntimeError, 'не подтверждён'): self.b.send('Task', [], 'stable', 'draft-test')
        self.assertEqual(len(self.rpc.starts), 1)
    def test_unknown_thread_start_never_creates_second_thread(self):
        self.rpc.lose_thread = True
        with self.assertRaises(TimeoutError): self.b.send('Task', [], 'stable', 'draft-test')
        with self.assertRaisesRegex(RuntimeError, 'неизвестный исход'): self.b.send('Task', [], 'stable', 'draft-test')
        self.assertEqual(len(self.rpc.created), 1)
    def test_operation_id_cannot_change_payload(self):
        self.rpc.lose_turn = True
        with self.assertRaises(TimeoutError): self.b.send('Task', [], 'stable', 'draft-test')
        with self.assertRaises(ValueError): self.b.send('Other', [], 'stable', 'draft-test')
    def busy(self):
        self.b.draft_threads = {}; self.b.send = lambda *args, **kwargs: (_ for _ in ()).throw(BusyError('busy'))
    def test_full_queue_does_not_accept_desktop_side_effect(self):
        self.busy(); self.b.queued_sends = {str(n): {'threadId': 'root'} for n in range(50)}
        with self.assertRaises(ValueError): self.b.submit('Task', [], 'full', 'root')
        self.assertEqual(self.rpc.adds, 0)
    def test_lost_native_queue_reply_reconciles_same_entry(self):
        self.busy(); self.rpc.lose_queue = True
        with self.assertRaises(TimeoutError): self.b.submit('Task', [], 'stable', 'root')
        self.assertTrue(self.b.queued_sends['stable']['nativePending'])
        self.b.reconcile_native_queue('root', self.rpc)
        self.assertEqual(self.b.queued_sends['stable']['nativeId'], 'native')
        self.b.submit('Task', [], 'stable', 'root')
        self.assertEqual(self.rpc.adds, 1)
    def test_unknown_queue_dismissal_is_not_cancellation_and_blocks_replay(self):
        self.b.queued_sends['stable'] = {'threadId': 'root', 'nativeId': 'missing', 'queueState': 'checking'}
        with self.assertRaises(ValueError): self.b.dismiss_unknown_message('stable')
        self.assertEqual(self.b.dismiss_unknown_message('stable', True)['status'], 'dismissed')
        self.assertNotIn('stable', self.b.cancelled_sends)
        self.assertFalse(self.b.queued_sends)
        with self.assertRaises(ValueError): self.b.send('Task', [], 'stable', 'root')
    def test_delete_does_not_hold_queue_while_waiting_rpc(self):
        owns_rpc = threading.Event(); in_list = threading.Event(); finished = threading.Event()
        rpc_lock = threading.RLock()
        @contextmanager
        def session():
            with rpc_lock: yield self.rpc
        self.b.rpc_session = session
        def listing(limit):
            in_list.set()
            with session(): return []
        self.b.list_threads = listing
        def worker():
            with rpc_lock:
                owns_rpc.set(); self.assertTrue(in_list.wait(2))
                with self.b.queue_lock: finished.set()
        t = threading.Thread(target=worker, daemon=True); t.start(); owns_rpc.wait(2)
        d = threading.Thread(target=lambda: self.b.delete_thread('draft-test'), daemon=True); d.start()
        d.join(3); t.join(3)
        self.assertTrue(finished.is_set()); self.assertFalse(d.is_alive()); self.assertFalse(t.is_alive())
    def test_duplicate_upload_reads_body_and_returns_same_file(self):
        first = self.b.upload('тест.txt', 'text/plain', io.BytesIO(b'content'), 7, client_upload_id='a'*32)
        stream = io.BytesIO(b'content')
        second = self.b.upload('тест.txt', 'text/plain', stream, 7, client_upload_id='a'*32)
        self.assertEqual(first, second); self.assertEqual(stream.tell(), 7)
        with self.assertRaises(ValueError): self.b.upload('тест.txt', 'text/plain', io.BytesIO(b'CORRUPT'), 7, client_upload_id='a'*32)
    def test_linked_file_resolves_with_missing_workspace(self):
        file = self.root/'linked.txt'; file.write_text('synthetic')
        self.b.draft_threads = {'child': str(self.root/'gone')}
        key = '@chat-files/'+'a'*64+'/linked.txt'
        self.b.chat_files[('child', key)] = file
        self.assertEqual(self.b.workspace_resolve('child', str(file))['path'], key)
        self.assertEqual(self.b.workspace_resolve('child', key)['path'], key)
        with self.assertRaises(ValueError): self.b.workspace_resolve('parent', str(file))


class RpcLifecycleTests(unittest.TestCase):
    def test_failed_initialization_closes_process_and_streams(self):
        process = MagicMock()
        process.stdout = io.StringIO("")
        process.stdin = io.StringIO()
        process.poll.return_value = None
        with patch("bridge.codex_path.resolve_codex_executable", return_value="synthetic"), \
             patch("bridge.server.subprocess.Popen", return_value=process), \
             patch.object(CodexRpc, "call", side_effect=RuntimeError("initialize failed")):
            with self.assertRaisesRegex(RuntimeError, "initialize failed"):
                CodexRpc()
        process.terminate.assert_called_once()
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)


if __name__ == '__main__': unittest.main()
