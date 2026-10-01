import threading
import unittest
from contextlib import contextmanager
from bridge.server import Bridge, RpcError


class ActivityLabelsTests(unittest.TestCase):
    def make_bridge(self, fails=False):
        bridge = Bridge.__new__(Bridge)
        bridge.agent_labels = {}
        bridge.agent_labels_lock = threading.Lock()
        calls = []

        class Rpc:
            def call(self, method, params):
                calls.append(params)
                if fails:
                    raise RpcError('offline')
                return {'thread': {'agentNickname': 'Gauss', 'model': 'gpt-6-luna',
                                   'agentRole': 'designer'}}

        @contextmanager
        def session():
            yield Rpc()

        bridge.rpc_session = session
        return bridge, calls

    def test_name_role_model_and_cache(self):
        bridge, calls = self.make_bridge()
        item = {'agentThreadId': 'child', 'agentPath': '/root/visual_design', 'kind': 'completed'}
        label = bridge.agent_activity_label(item)
        self.assertEqual(label, 'Завершил работу · Gauss · designer · gpt-6-luna')
        self.assertEqual(bridge.agent_activity_label(item), label)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]['includeTurns'])

    def test_failure_uses_readable_path_and_is_cached(self):
        bridge, calls = self.make_bridge(True)
        item = {'agentThreadId': 'child', 'agentPath': '/root/visual_design'}
        self.assertEqual(bridge.agent_activity_label(item), 'Субагент · visual design')
        bridge.agent_activity_label(item)
        self.assertEqual(len(calls), 1)

    def test_old_activity_does_not_fetch(self):
        bridge, calls = self.make_bridge()
        self.assertEqual(bridge.agent_activity_label({'agentThreadId': 'old'}, False),
                         'Субагент')
        self.assertEqual(calls, [])
