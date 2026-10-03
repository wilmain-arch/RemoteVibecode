"""Inert RPC tests: no accounts, live tasks or file changes."""
import io
import queue
import threading
import unittest
from unittest.mock import Mock
from bridge.interactions import InteractionInbox
from bridge.server import Bridge, CodexRpc


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.inbox = InteractionInbox(self.sent.append)

    def add(self, method='item/commandExecution/requestApproval', **params):
        self.inbox.receive({'method': method, 'id': 1, 'params': {'threadId': 'root', **params}})
        return self.inbox.list('root')[0]['id']

    def test_once_only_and_identical_retry(self):
        key = self.add()
        for _ in range(2): self.inbox.respond('root', key, {'decision': 'accept'})
        self.assertEqual(self.sent, [{'id': 1, 'result': {'decision': 'accept'}}])
        with self.assertRaises(ValueError): self.inbox.respond('root', key, {'decision': 'decline'})

    def test_wrong_thread_stale_session_and_resolved(self):
        key = self.add()
        with self.assertRaises(ValueError): self.inbox.respond('other', key, {'decision': 'accept'})
        with self.assertRaises(ValueError): InteractionInbox(self.sent.append).respond('root', key, {'decision': 'accept'})
        self.inbox.resolve(1)
        with self.assertRaises(ValueError): self.inbox.respond('root', key, {'decision': 'accept'})
        self.assertEqual(self.sent, [])

    def test_questions_validate_all_and_options(self):
        key = self.add('item/tool/requestUserInput', questions=[{'id': 'q', 'options': [{'label': 'Да'}], 'isOther': False}])
        with self.assertRaises(ValueError): self.inbox.respond('root', key, {'answers': {}})
        with self.assertRaises(ValueError): self.inbox.respond('root', key, {'answers': {'q': {'answers': ['Нет']}}})
        self.inbox.respond('root', key, {'answers': {'q': {'answers': ['Да']}}})
        self.assertEqual(self.sent[0]['result']['answers']['q']['answers'], ['Да'])

    def test_permissions_cannot_be_widened(self):
        requested = {'network': {'enabled': True}, 'fileSystem': None}
        key = self.add('item/permissions/requestApproval', permissions=requested)
        self.inbox.respond('root', key, {'decision': 'accept', 'scope': 'session', 'permissions': {'fileSystem': {'write': ['/']}}})
        self.assertEqual(self.sent[0]['result'], {'permissions': {'network': {'enabled': True}}, 'scope': 'turn'})

    def test_decline_permissions_and_advertised_decisions(self):
        key = self.add('item/permissions/requestApproval', permissions={'network': {'enabled': True}})
        self.inbox.respond('root', key, {'decision': 'decline'})
        self.assertEqual(self.sent[0]['result']['permissions'], {})
        self.inbox = InteractionInbox(self.sent.append)
        key = self.add(availableDecisions=['decline'])
        with self.assertRaises(ValueError): self.inbox.respond('root', key, {'decision': 'accept'})

    def test_send_failure_retains_prompt(self):
        key = self.add()
        self.inbox.send = Mock(side_effect=OSError('offline'))
        with self.assertRaises(OSError): self.inbox.respond('root', key, {'decision': 'accept'})
        self.assertEqual(len(self.inbox.list('root')), 1)

    def test_request_id_collision_does_not_answer_outbound_call(self):
        rpc = CodexRpc.__new__(CodexRpc)
        rpc.proc = Mock(stdout=io.StringIO('{"id":1,"method":"item/tool/requestUserInput","params":{"threadId":"root","questions":[]}}\n'))
        rpc._waiters_lock = threading.Lock(); waiter = queue.Queue(); rpc._waiters = {1: waiter}
        rpc._send = self.sent.append; rpc.interactions = self.inbox; rpc.on_event = Mock()
        rpc._read_loop()
        self.assertTrue(waiter.empty()); self.assertEqual(len(self.inbox.list('root')), 1)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.selected_thread_id = 'root'; self.bridge.thread_id = 'root'
        self.bridge.draft_threads = {}; self.bridge.rpc_lock = threading.RLock()
        self.bridge.rpc_last_used = 0; self.bridge.rpc = Mock()
        self.bridge.rpc.proc.poll.return_value = None
        self.bridge.rpc.call.return_value = {'thread': {'turns': [{'id': 'current', 'status': 'inProgress'}]}}
        self.bridge._signal_event = Mock()

    def test_stop_rejects_new_turn_and_interrupts_expected(self):
        with self.assertRaises(ValueError): self.bridge.interrupt_turn({'threadId': 'root', 'turnId': 'old'})
        self.assertEqual(self.bridge.rpc.call.call_count, 1)
        self.bridge.interrupt_turn({'threadId': 'root', 'turnId': 'current'})
        self.bridge.rpc.call.assert_called_with('turn/interrupt', {'threadId': 'root', 'turnId': 'current'})

    def test_completed_stop_is_noop(self):
        self.bridge.rpc.call.return_value = {'thread': {'turns': [{'id': 'current', 'status': 'completed'}]}}
        self.assertTrue(self.bridge.interrupt_turn({'threadId': 'root', 'turnId': 'current'})['accepted'])
        self.assertEqual(self.bridge.rpc.call.call_count, 1)

    def test_diff_preserves_paths_and_bounds_output(self):
        self.bridge.rpc.call.return_value = {'thread': {'turns': [{'id': 'current', 'items': [
            {'id': 'patch', 'type': 'fileChange', 'status': 'completed', 'changes': [
                {'path': '/project/file.kt', 'kind': {'type': 'update'}, 'diff': '+' + 'x' * 200000}]}]}]}}
        result = self.bridge.changes('root', 'current')
        self.assertTrue(result['truncated']); self.assertEqual(result['changes'][0]['path'], '/project/file.kt')
        self.assertEqual(len(result['changes'][0]['diff']), 128*1024)

if __name__ == '__main__': unittest.main()

class DescendantRequestTests(unittest.TestCase):
    def test_root_sees_child_but_unrelated_requests_are_hidden(self):
        bridge = Bridge.__new__(Bridge)
        bridge.selected_thread_id = 'root'; bridge.thread_id = 'root'
        bridge.agent_parents = {}; bridge.agent_parents_lock = threading.RLock()
        bridge.rpc = Mock(); bridge.rpc.proc.poll.return_value = None
        bridge.rpc.interactions = InteractionInbox(lambda _: None)
        bridge._remember_agent_relations('root', [{'type': 'subAgentActivity', 'agentThreadId': 'child'}])
        bridge._remember_agent_relations('child', [{'type': 'collabAgentToolCall', 'tool': 'spawnAgent', 'receiverThreadIds': ['nested']}])
        for i, thread in enumerate(['root', 'child', 'nested', 'unrelated']):
            bridge.rpc.interactions.receive({'id': i, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': thread}})
        self.assertEqual({x['threadId'] for x in bridge.requests('root')['requests']}, {'root', 'child', 'nested'})
