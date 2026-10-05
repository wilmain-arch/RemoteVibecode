"""Installed Codex + real isolated executor, no provider/model requests."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import json
import time
import queue
from tests.guest_mock_provider import MockProvider
import unittest
from bridge.guest_executor_gateway import ExecutorGateway
from bridge.server import CodexRpc
from bridge.guest_runner import GuestRunner
from tests.test_guest_sandbox import CODEX

@unittest.skipUnless(sys.platform=='linux' and shutil.which('bwrap') and CODEX.is_file(),'Linux executor required')
class GuestEnvironmentTests(unittest.TestCase):
    def test_register_and_start_isolated_environment_without_generation(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-environment-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            gateway=ExecutorGateway(work,CODEX)
            rpc=None
            try:
                rpc=CodexRpc(executable_override=str(CODEX),process_env={'HOME':str(home),'CODEX_HOME':str(home),'PATH':'/usr/bin'},process_cwd=str(home))
                rpc.call('environment/add',{'environmentId':'rv-guest-fixture','execServerUrl':gateway.url,'authBearerToken':gateway.token,'connectTimeoutMs':3000})
                info=rpc.call('environment/info',{'environmentId':'rv-guest-fixture'})
                self.assertEqual(info['cwd'],'file:///workspace')
                status=rpc.call('environment/status',{'environmentId':'rv-guest-fixture'})
                self.assertEqual(status['status'],'ready')
                thread=rpc.call('thread/start',{'ephemeral':True,'cwd':'/workspace','approvalPolicy':'never','sandbox':'workspace-write',
                    'environments':[{'environmentId':'rv-guest-fixture','cwd':'/workspace','runtimeWorkspaceRoots':['/workspace']}]})['thread']
                self.assertEqual(thread['environments'][0]['environmentId'],'rv-guest-fixture')
            finally:
                if rpc:rpc.close()
                gateway.close()

    def test_model_tool_executes_only_in_guest_environment(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-turn-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            (work/'fixture.txt').write_text('guest-workspace-visible')
            outside=root/'synthetic-owner-secret';outside.write_text('must-not-be-visible')
            provider=MockProvider('pwd; cat /workspace/fixture.txt; if test -e '+str(outside)+'; then echo ESCAPED; else echo ISOLATED; fi')
            (home/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
            gateway=ExecutorGateway(work,CODEX);rpc=None
            try:
                rpc=CodexRpc(executable_override=str(CODEX),process_env={'HOME':str(home),'CODEX_HOME':str(home),'PATH':'/usr/bin'},process_cwd=str(home))
                rpc.call('environment/add',{'environmentId':'rv-guest-fixture','execServerUrl':gateway.url,'authBearerToken':gateway.token,'connectTimeoutMs':3000})
                thread=rpc.call('thread/start',{'ephemeral':True,'cwd':'/workspace','approvalPolicy':'never','sandbox':'workspace-write',
                    'environments':[{'environmentId':'rv-guest-fixture','cwd':'/workspace','runtimeWorkspaceRoots':['/workspace']}]})['thread']
                started=rpc.call('turn/start',{'threadId':thread['id'],'input':[{'type':'text','text':'Synthetic tool fixture','text_elements':[]}],
                    'approvalPolicy':'never','sandboxPolicy':{'type':'workspaceWrite','writableRoots':['/workspace'],'networkAccess':False},
                    'environments':[{'environmentId':'rv-guest-fixture','cwd':'/workspace','runtimeWorkspaceRoots':['/workspace']}]})
                deadline=time.monotonic()+15;completed=None
                while time.monotonic()<deadline:
                    try:event=rpc.events.get(timeout=1)
                    except queue.Empty:continue
                    if event.get('method')=='turn/completed':completed=event;break
                self.assertIsNotNone(completed)
                self.assertEqual(completed['params']['turn']['status'],'completed',completed)
                self.assertEqual(len(provider.requests),2)
                encoded=json.dumps([i for i in provider.requests[1]['input'] if i.get('type')=='function_call_output'])
                self.assertIn('guest-workspace-visible',encoded)
                self.assertIn('ISOLATED',encoded)
                self.assertNotIn('ESCAPED',encoded)
            finally:
                if rpc:rpc.close()
                gateway.close();provider.close()

    def test_runner_returns_real_native_turn_with_mock_provider(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-runner-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            provider=MockProvider('pwd')
            try:
                (home/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
                starts=[]
                progress=[]
                runner=GuestRunner(workspace=work,broker=home,codex=CODEX,rpc_factory=CodexRpc,
                    on_started=lambda task,thread,turn:starts.append((thread,turn)),
                    on_progress=lambda task,messages:progress.append(messages),timeout=15)
                result=runner({'operationId':'runner-fixture-001','text':'Synthetic task'},lambda:{'stop':False})
                self.assertEqual(result['status'],'completed')
                self.assertEqual(len(starts),1)
                self.assertTrue(progress)
                self.assertTrue(any(progress[-1]))
                self.assertEqual(len(provider.requests),2)
                self.assertIn('/workspace',json.dumps([i for i in provider.requests[1]['input'] if i.get('type')=='function_call_output']))
            finally:provider.close()

    def test_unadvertised_patch_tool_is_rejected_without_host_write(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-patch-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            outside=root/'synthetic-owner.txt';outside.write_text('owner unchanged')
            patch_text='*** Begin Patch\n*** Add File: /workspace/patched.txt\n+guest patch\n*** End Patch'
            provider=MockProvider('',{'id':'custom_fixture','type':'custom_tool_call','call_id':'call_fixture','name':'apply_patch','input':patch_text})
            try:
                (home/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
                result=GuestRunner(workspace=work,broker=home,codex=CODEX,rpc_factory=CodexRpc,timeout=15)(
                    {'operationId':'apply-patch-fixture','text':'Synthetic patch'},lambda:{'stop':False})
                self.assertEqual(result['status'],'completed')
                self.assertFalse((work/'patched.txt').exists())
                outputs=[item for item in provider.requests[1]['input'] if item.get('type')=='custom_tool_call_output']
                self.assertIn('unsupported custom tool',json.dumps(outputs))
                self.assertEqual(outside.read_text(),'owner unchanged')
            finally:provider.close()

    def test_image_tool_cannot_read_outside_guest_workspace(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-image-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            from PIL import Image
            outside=root/'synthetic-private.png';Image.new('RGB',(8,8),'red').save(outside)
            provider=MockProvider('',{'id':'image_fixture','type':'function_call','call_id':'call_fixture','name':'view_image','arguments':json.dumps({'path':str(outside)})})
            try:
                (home/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
                result=GuestRunner(workspace=work,broker=home,codex=CODEX,rpc_factory=CodexRpc,timeout=15)(
                    {'operationId':'image-isolation-fixture','text':'Synthetic image'},lambda:{'stop':False})
                self.assertEqual(result['status'],'completed')
                outputs=[item for item in provider.requests[1]['input'] if item.get('type')=='function_call_output']
                encoded=json.dumps(outputs)
                self.assertNotIn('data:image',json.dumps(provider.requests[1]['input']))
                self.assertNotIn('input_image',encoded)
                self.assertTrue(outputs)
            finally:provider.close()

    def test_runner_interrupts_native_turn_without_paid_requests(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-stop-') as temp:
            root=Path(temp);work=root/'workspace';work.mkdir();home=root/'broker';home.mkdir()
            provider=MockProvider('sleep 5; echo should-not-finish')
            try:
                (home/'config.toml').write_text('model="fixture"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="'+provider.url+'"\nwire_api="responses"\nrequires_openai_auth=false\n')
                runner=GuestRunner(workspace=work,broker=home,codex=CODEX,rpc_factory=CodexRpc,timeout=15)
                result=runner({'operationId':'runner-stop-fixture','text':'Synthetic interrupt'},lambda:{'stop':True})
                self.assertEqual(result['status'],'interrupted')
                self.assertLessEqual(len(provider.requests),1)
            finally:provider.close()

if __name__=='__main__':unittest.main()
