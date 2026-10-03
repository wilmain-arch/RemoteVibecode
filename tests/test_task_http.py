"""Authenticated production HTTP routes backed by an inert fake app-server."""
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import urllib.request
import urllib.error

FAKE = '''#!/usr/bin/env python3
import sys,json
started=False
finished=False
planned=False
def send(data): print(json.dumps(data),flush=True)
for line in sys.stdin:
 data=json.loads(line); method=data.get('method'); ident=data.get('id')
 if method=='initialized': continue
 if not method:
  send({'method':'serverRequest/resolved','params':{'threadId':'fixture-root','requestId':ident}})
  continue
 if method=='initialize': result={}
 elif method=='thread/read':
  if not started:
   started=True
   send({'id':101,'method':'item/tool/requestUserInput','params':{'threadId':'fixture-root','turnId':'fixture-turn','questions':[{'id':'q','question':'Выберите вариант','options':[{'label':'Фокус'}],'isOther':False}]}})
  result={'thread':{'id':'fixture-root','name':'Synthetic fixture','status':{'type':'idle' if finished else 'active'},'turns':[{'id':'fixture-turn','status':'completed' if finished else 'inProgress','startedAt':1,'completedAt':2 if finished else None,'items':[{'id':'patch','type':'fileChange','status':'completed','changes':[{'path':'/synthetic/Main.kt','kind':{'type':'update'},'diff':'@@ -1 +1 @@\\n-old\\n+new'}]}]}]}}
  result['thread']['model']='synthetic-model'
  if planned: result['thread']['turns'].append({'id':'new-plan','status':'inProgress','items':[{'id':'new-plan-item','type':'plan','text':'Synthetic new plan'}]})
 elif method=='turn/start':
  assert data['params']['collaborationMode']['mode']=='plan'
  assert data['params']['clientUserMessageId']=='plan-operation'
  planned=True; result={'turn':{'id':'new-plan'}}
 elif method=='turn/interrupt': finished=True; result={}
 elif method=='thread/searchOccurrences': result={'data':[{'turnId':'fixture-turn','itemId':'item','snippet':'synthetic match','turnCursor':'cursor'}],'nextCursor':None}
 elif method=='thread/queue/list': result={'data':[],'nextCursor':None}
 elif method=='thread/goal/set': result={'goal':{'objective':data['params']['objective'],'status':data['params']['status']}}
 elif method=='collaborationMode/list': result={'data':[{'mode':'plan'},{'mode':'default'}]}
 elif method=='thread/fork': result={'thread':{'id':'fixture-fork'}}
 else: result={}
 send({'id':ident,'result':result})
'''

class TaskHttpTests(unittest.TestCase):
    def test_authenticated_question_diff_stop_and_result(self):
        with tempfile.TemporaryDirectory(prefix='rv-task-http-') as temp:
            root=Path(temp); executable=root/'fake-codex'; executable.write_text(FAKE); executable.chmod(0o700)
            cert=root/'cert.pem'; key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=127.0.0.1',
                '-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            state=root/'state.json'; state.write_text(json.dumps({'thread_id':'fixture-root','paired':True,'token':'synthetic-http-token','selected_thread_id':'fixture-root'}))
            with socket.socket() as sock: sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={**os.environ,'CODEX_EXECUTABLE':str(executable)}
            proc=subprocess.Popen(['python','-m','bridge.server','--thread','fixture-root','--bind','127.0.0.1','--port',str(port),
                '--cert',str(cert),'--key',str(key),'--state',str(state),'--inbox',str(root/'inbox'),'--outbox',str(root/'outbox')],
                env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            context=ssl.create_default_context(cafile=str(cert))
            def call(path, body=None, authorized=True):
                req=urllib.request.Request(f'https://127.0.0.1:{port}/api/'+path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization':'Bearer synthetic-http-token'} if authorized else {})
                with urllib.request.urlopen(req,context=context,timeout=5) as response: return json.load(response)
            try:
                deadline=time.monotonic()+5
                while True:
                    try: status=call('status?threadId=fixture-root');break
                    except (OSError,urllib.error.URLError):
                        if time.monotonic()>deadline:raise
                        time.sleep(.05)
                self.assertEqual(status['activeTurnId'],'fixture-turn')
                with self.assertRaises(urllib.error.HTTPError) as error: call('requests?threadId=fixture-root',authorized=False)
                self.assertEqual(error.exception.code,401)
                prompt=call('requests?threadId=fixture-root')['requests'][0]
                answer={'threadId':'fixture-root','id':prompt['id'],'response':{'answers':{'q':{'answers':['Фокус']}}}}
                self.assertTrue(call('requests/respond',answer)['accepted'])
                self.assertTrue(call('requests/respond',answer)['accepted'])
                self.assertEqual(call('changes?threadId=fixture-root')['changes'][0]['diff'],'@@ -1 +1 @@\n-old\n+new')
                plan_request={'threadId':'fixture-root','action':'plan-create','text':'Synthetic planning request', 'operationId':'plan-operation','confirmed':True}
                with self.assertRaises(urllib.error.HTTPError) as busy:
                    call('control/action',plan_request)
                self.assertEqual(busy.exception.code,400)
                self.assertTrue(call('turn/interrupt',{'threadId':'fixture-root','turnId':'fixture-turn'})['accepted'])
                self.assertEqual(call('task?threadId=fixture-root&turnId=fixture-turn')['status'],'completed')
                self.assertEqual(call('control?threadId=fixture-root&section=search&query=synthetic')['data'][0]['turnId'],'fixture-turn')
                self.assertEqual(call('control/action',{'threadId':'fixture-root','action':'rename','name':'Renamed fixture'}),{})
                self.assertEqual(call('control/action',{'threadId':'fixture-root','action':'goal-set','objective':'Paused fixture'})['goal']['status'],'paused')
                self.assertEqual(call('control/action',{'threadId':'fixture-root','action':'fork','turnId':'fixture-turn'})['threadId'],'fixture-fork')
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    call('control/action',{'threadId':'fixture-root','action':'revert','turnId':'fixture-turn'})
                self.assertEqual(invalid.exception.code,400)
                created=call('control/action',plan_request)
                self.assertEqual(created['turnId'],'new-plan')
                self.assertEqual(call('control/action',plan_request),created)
                planning=call('control?threadId=fixture-root&section=plan')
                self.assertEqual(planning['currentPlanTurnId'],'new-plan')
                self.assertEqual(planning['selectedMode'],'default')
                self.assertEqual(json.loads(state.read_text())['plan_boundaries']['fixture-root'],'new-plan')
                self.assertEqual(planning['plans'][0]['text'],'Synthetic new plan')
                with self.assertRaises(urllib.error.HTTPError) as collision:
                    call('control/action',dict(plan_request,text='Different request'))
                self.assertEqual(collision.exception.code,400)
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    call('control?threadId=fixture-root&section=queue',authorized=False)
                self.assertEqual(unauthorized.exception.code,401)
                proc.terminate(); proc.wait(timeout=5)
                proc=subprocess.Popen(proc.args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+5
                while True:
                    try: restored=call('control?threadId=fixture-root&section=plan');break
                    except (OSError,urllib.error.URLError):
                        if time.monotonic()>deadline: raise
                        time.sleep(.05)
                self.assertEqual(restored['currentPlanTurnId'],'new-plan')
                self.assertEqual(restored['plans'],[])
                self.assertEqual(call('control/action',plan_request),created)

            finally:
                proc.terminate(); proc.wait(timeout=5)
