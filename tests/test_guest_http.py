"""Production TLS handler tests; no real account or generation calls."""
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from tests.test_task_http import FAKE


class GuestHttpTests(unittest.TestCase):
    def test_owner_guest_separation_and_revoke(self):
        with tempfile.TemporaryDirectory(prefix='rv-guest-http-') as temp:
            root=Path(temp)
            fake=root/'codex';fake.write_text(FAKE);fake.chmod(0o700)
            cert=root/'cert.pem';key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=127.0.0.1',
                            '-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            state=root/'state.json';state.write_text(json.dumps({'thread_id':'fixture-root','paired':True,'token':'owner-fixture','selected_thread_id':'fixture-root'}))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            proc=subprocess.Popen(['python','-m','bridge.server','--thread','fixture-root','--bind','127.0.0.1','--port',str(port),
                                   '--cert',str(cert),'--key',str(key),'--state',str(state),'--inbox',str(root/'inbox'),'--outbox',str(root/'outbox')],
                                  env={**os.environ,'CODEX_EXECUTABLE':str(fake)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            ctx=ssl.create_default_context(cafile=str(cert))
            def call(path,data=None,token='owner-fixture'):
                req=urllib.request.Request(f'https://127.0.0.1:{port}/api/'+path,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(req,context=ctx,timeout=5) as response:return json.load(response)
            try:
                deadline=time.monotonic()+5
                while True:
                    try:call('guests');break
                    except OSError:
                        if time.monotonic()>deadline:raise
                        time.sleep(.05)
                secret=secrets.token_urlsafe(32)
                invite={'operationId':'invite-fixture-001','secret':secret,'name':'Synthetic guest',
                        'quotas':{'fiveHours':{'mode':'unlimited'},'week':{'mode':'unlimited'}}}
                created=call('guests/invite',invite)
                self.assertEqual(call('guests/invite',invite),created)
                guest_token=secrets.token_urlsafe(32)
                redeemed=call('guest/redeem',{'secret':secret,'deviceId':'fixture-device','sessionToken':guest_token},token='')
                self.assertEqual(redeemed['role'],'guest')
                self.assertFalse(redeemed['executionAvailable'])
                self.assertEqual(call('guest/self',token=guest_token)['id'],created['guestId'])
                self.assertEqual(call('guest/tasks',token=guest_token),{'tasks':[],'nextCursor':None})
                for token,code in ((guest_token,503),('owner-fixture',403)):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        call('guest/send',{'operationId':'task-fixture-001','text':'Synthetic'},token)
                    self.assertEqual(error.exception.code,code)
                    error.exception.close()
                from bridge.guest_access import GuestAccess
                from bridge.guest_jobs import GuestJobs
                access=GuestAccess(root/'guest-access.sqlite3')
                try:
                    jobs=GuestJobs(access)
                    request={'operationId':'accepted-before-disable','text':'Synthetic accepted task'}
                    jobs.enqueue(created['guestId'],request);jobs.update(request['operationId'],'completed')
                    accepted=jobs.get(created['guestId'],request['operationId'])
                finally:access.db.close()
                self.assertEqual(call('guest/send',request,guest_token),accepted)
                self.assertEqual(call('guest/cancel',{'operationId':'unknown-fixture'},guest_token)['state'],'cancelled')
                with self.assertRaises(urllib.error.HTTPError) as error:call('guest/tasks?before=foreign-fixture',token=guest_token)
                self.assertEqual(error.exception.code,400);error.exception.close()
                for path in ('guests','guests/copies?guestId=fixture','status','history?threadId=fixture-root','events','projects','threads','models','limits',
                             'adb/devices','outbox','workspace?path=/','chat/image?id=fixture','download?id=fixture','control?section=search'):
                    with self.subTest(path=path):
                        with self.assertRaises(urllib.error.HTTPError) as error:call(path,token=guest_token)
                        self.assertIn(error.exception.code,(401,403))
                for path in ('send','control/action','threads','threads/delete','unpair','limits/reset','adb/connect','subagents/action','guests/invite','guests/action'):
                    with self.subTest(path=path):
                        with self.assertRaises(urllib.error.HTTPError) as error:call(path,{},guest_token)
                        self.assertIn(error.exception.code,(401,403))
                call('guests/action',{'operationId':'revoke-fixture-001','guestId':created['guestId'],'action':'revoke'})
                with self.assertRaises(urllib.error.HTTPError) as error:call('guest/self',token=guest_token)
                self.assertEqual(error.exception.code,401)
                self.assertEqual(call('guests')['guests'],[])
            finally:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__=='__main__':unittest.main()
