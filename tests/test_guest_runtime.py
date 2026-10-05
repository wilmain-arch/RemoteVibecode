"""Service loop integration: native Codex, synthetic provider and meter only."""
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from bridge.guest_access import GuestAccess,AccessError
from bridge.guest_jobs import GuestJobs
from bridge.guest_runtime import GuestRuntime,desktop_present
from bridge.guest_resources import GuestResources
from tests.guest_mock_provider import MockProvider
from tests.test_guest_sandbox import CODEX
from tests.test_guest_access import sample

class DesktopPresenceTest(unittest.TestCase):
    def process(self, root, pid, executable, name):
        entry=root/str(pid);entry.mkdir()
        (entry/'comm').write_text(name)
        (entry/'exe').symlink_to(executable)

    def test_bridge_resource_helpers_are_not_desktop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.process(root,101,'/usr/lib/chatgpt/resources/codex','codex')
            self.process(root,102,'/usr/lib/chatgpt/resources/cua_node/bin/node','MainThread')
            self.process(root,103,'/usr/lib/chatgpt/resources/cua_node/bin/node_repl','node_repl')
            self.process(root,104,'/usr/lib/chatgpt/resources/codex-code-mode-host','codex-code-mode')
            with patch('bridge.guest_runtime.sys.platform','linux'):
                self.assertFalse(desktop_present(proc_root=root))

    def test_actual_desktop_executable_blocks_even_if_thread_renamed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.process(root,101,'/usr/lib/chatgpt/ChatGPT','MainThread')
            with patch('bridge.guest_runtime.sys.platform','linux'):
                self.assertTrue(desktop_present(proc_root=root))

    def test_update_arm_prevents_guest_launch_until_release(self):
        runtime=GuestRuntime.__new__(GuestRuntime)
        runtime.enabled=True;runtime.owner_idle=lambda:True
        runtime.bridge=SimpleNamespace(paired=True,update_until=time.monotonic()+120,
            queue_lock=threading.Lock(),queued_sends={})
        self.assertFalse(runtime.can_launch())
        runtime.bridge.update_until=0
        self.assertTrue(runtime.can_launch())

    def test_missing_process_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('bridge.guest_runtime.sys.platform','linux'):
                self.assertTrue(desktop_present(proc_root=Path(temp)/'missing'))


@unittest.skipUnless(sys.platform=='linux' and shutil.which('bwrap') and CODEX.is_file(),'Linux executor required')
class RuntimeTest(unittest.TestCase):
    def test_account_change_disables_executor_and_revokes_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);auth=root/'auth.json'
            auth.write_text(json.dumps({'tokens':{'account_id':'synthetic-account-one'}}))
            access=GuestAccess(root/'guests.sqlite3');saved=[]
            bridge=SimpleNamespace(guests=access,state_lock=threading.RLock(),guest_execution_enabled=True,_save_state=lambda:saved.append(True))
            runtime=GuestRuntime.__new__(GuestRuntime);runtime.bridge=bridge;runtime.enabled=True
            try:
                with patch.dict('os.environ',{'CODEX_HOME':str(root)}):
                    runtime.account_auth()
                    secret=secrets.token_urlsafe(32);token=secrets.token_urlsafe(32)
                    guest=access.invite({'operationId':'auth-switch-fixture','name':'Fixture','secret':secret,'quotas':{}},sample())['guestId']
                    access.redeem(secret,'device-fixture',token)
                    auth.write_text(json.dumps({'tokens':{'account_id':'synthetic-account-two'}}))
                    with self.assertRaises(ValueError):runtime.account_auth()
                    self.assertFalse(runtime.enabled);self.assertFalse(bridge.guest_execution_enabled)
                    self.assertTrue(saved)
                    with self.assertRaises(AccessError):access.authenticate(token)
            finally:access.db.close()

    def test_queue_to_native_result_without_owner_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();broker=root/'broker';broker.mkdir()
            provider=MockProvider('pwd; echo queue-native-result')
            access=GuestAccess(root/'guests.sqlite3');jobs=GuestJobs(access)
            secret=secrets.token_urlsafe(32)
            guest=access.invite({'operationId':'invite-runtime-fixture','secret':secret,'name':'Fixture',
                'quotas':{'fiveHours':{'mode':'unlimited'},'week':{'mode':'unlimited'}}},sample())['guestId']
            access.redeem(secret,'device-fixture',secrets.token_urlsafe(32))
            bridge=SimpleNamespace(state_file=root/'state.json',guests=access,guest_jobs=jobs,guest_usage_limits=sample,
                send_lock=threading.Lock(),turn_lock=threading.Lock(),queue_lock=threading.Lock(),active_turns={},queued_sends={},paired=True)
            bridge.guest_resources=GuestResources(bridge)
            (broker/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
            runtime=None
            try:
                with patch('bridge.guest_runtime.desktop_present',return_value=False):
                    runtime=GuestRuntime(bridge)
                    runtime.codex=CODEX
                    runtime.provision=lambda guest:(work,broker)
                    runtime.enabled=True
                    first=jobs.enqueue(guest,{'operationId':'runtime-task-fixture','text':'Synthetic fixture'})
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        task=jobs.get(guest,first['operationId'])
                        if task['state'] in ('completed','uncertain','failed'):break
                        time.sleep(.1)
                    self.assertEqual(task['state'],'completed',task)
                    self.assertEqual(task['result']['output']['status'],'completed')
                    self.assertTrue(task['threadId'])
                    self.assertEqual(jobs.conversation(guest,'default-main'),task['threadId'])
                    self.assertEqual(len(provider.requests),2)
                    jobs.enqueue(guest,{'operationId':'runtime-task-fixture','text':'Synthetic fixture'})
                    self.assertEqual(len(provider.requests),2)
            finally:
                if runtime:runtime.close()
                provider.close();access.db.close()
