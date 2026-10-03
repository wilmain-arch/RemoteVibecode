"""Second-stage controls use inert RPC fixtures and isolated Git repositories."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from bridge.controls import Controls, value, ident
from bridge.server import Bridge, BusyError

class FakeRpc:
    def __init__(self):
        self.calls=[];self.active=False;self.queue=[]
    def call(self, method, params):
        self.calls.append((method,params))
        if method=='thread/read':return {'thread':{'id':'root','cwd':'/synthetic','turns':[{'id':'turn','status':'inProgress' if self.active else 'completed','items':[]}]}}
        if method=='thread/queue/list':return {'data':self.queue,'nextCursor':None}
        if method=='thread/queue/add':return {'queuedSubmission':{'id':'native-id'}}
        if method=='collaborationMode/list':return {'data':[{'mode':'plan'},{'mode':'default'}]}
        if method=='thread/fork':return {'thread':{'id':'fork'}}
        return {}

class FakeBridge:
    def __init__(self, root):
        self.root=root;self.rpc=FakeRpc();self.draft_threads={};self.queued_sends={};self.cancelled_sends={};self.thread_modes={};self.sent_messages={};self.pending={};self.published_plans={};self.control_operations={}
        self.queue_lock=threading.Lock();self.state_lock=threading.RLock();self.send_lock=threading.Lock();self.saved=0
    def _thread_id(self,tid):
        return ident({'id':tid or 'root'},'id')
    @contextmanager
    def rpc_session(self):yield self.rpc
    def _save_state(self):self.saved+=1
    def list_threads(self,limit):return [{'id':'next'}]
    def workspace_path(self,tid,path):
        p=(self.root/path).resolve()
        if not p.is_relative_to(self.root):raise ValueError('outside root')
        return self.root,p

class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.b=FakeBridge(self.root);self.c=Controls(self.b)
    def test_rename_is_bounded(self):
        self.c.action('root','rename',{'name':'Русское название'})
        self.assertEqual(self.b.rpc.calls[-1],('thread/name/set',{'threadId':'root','name':'Русское название'}))
        with self.assertRaises(ValueError):self.c.action('root','rename',{'name':'x'*201})
    def test_revert_requires_confirmation_and_exact_turn(self):
        for body in ({'turnId':'turn'}, {'turnId':'wrong','confirmed':True}):
            with self.assertRaises(ValueError):self.c.action('root','revert',body)
        self.c.action('root','revert',{'turnId':'turn','confirmed':True})
        self.assertEqual(self.b.rpc.calls[-1],('thread/revert',{'threadId':'root','beforeTurnId':'turn'}))
    def test_active_chat_cannot_archive_or_fork(self):
        self.b.rpc.active=True
        for action in ['archive','fork']:
            with self.assertRaises(ValueError):self.c.action('root',action,{'turnId':'turn'})
    def test_archive_selects_other_chat(self):
        self.assertEqual(self.c.action('root','archive',{})['threadId'],'next')
    def test_fork_includes_turn_defers_goal(self):
        self.assertEqual(self.c.action('root','fork',{'turnId':'turn'})['threadId'],'fork')
        self.assertTrue(self.b.rpc.calls[-1][1]['deferGoalContinuation'])
    def test_goal_is_paused_and_no_turn_is_started(self):
        self.c.action('root','goal-set',{'objective':'Test'})
        self.assertEqual(self.b.rpc.calls[-1][1]['status'],'paused')
        self.assertNotIn('turn/start',[m for m,p in self.b.rpc.calls])
    def test_goal_status_rejects_automatic_resume(self):
        with self.assertRaises(ValueError):self.c.action('root','goal-status',{'status':'active'})
    def test_mode_uses_advertised_modes_and_saved_next_turn(self):
        self.c.action('root','mode',{'mode':'plan'});self.assertEqual(self.b.thread_modes['root'],'plan')
        with self.assertRaises(ValueError):self.c.action('root','mode',{'mode':'invented'})
    def test_queue_launch_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):self.c.action('root','queue-add',{'text':'test','messageId':'id'})
    def test_native_queue_form_retry_reuses_existing_entry(self):
        self.b.rpc.queue = [{'id': 'existing', 'clientUserMessageId': 'client', 'input': []}]
        result = self.c.action('root', 'queue-add', {'text': 'test', 'messageId': 'client', 'confirmed': True})
        self.assertEqual(result['queuedSubmission']['id'], 'existing')
        self.assertFalse(any(method == 'thread/queue/add' for method, _ in self.b.rpc.calls))

    def test_native_queue_form_retry_after_delivery_does_not_start_again(self):
        original = self.b.rpc.call
        def call(method, params):
            result = original(method, params)
            if method == 'thread/read':
                result['thread']['turns'][0]['items'] = [{'type': 'userMessage', 'clientId': 'client'}]
            return result
        self.b.rpc.call = call
        result = self.c.action('root', 'queue-add', {'text': 'test', 'messageId': 'client', 'confirmed': True})
        self.assertTrue(result['alreadyDelivered'])
        self.assertFalse(any(method == 'thread/queue/add' for method, _ in self.b.rpc.calls))

    def test_queue_update_cannot_discard_attachment(self):
        self.b.rpc.queue=[{'id':'q','input':[{'type':'localImage','path':'/tmp/a'}]}]
        with self.assertRaises(ValueError):self.c.action('root','queue-update',{'submissionId':'q','text':'changed'})
    def test_queue_reorder_rejects_stale_or_duplicate_list(self):
        self.b.rpc.queue=[{'id':'a'},{'id':'b'}]
        for ids in [['a','a'],['a'],[['nested']]]:
            with self.assertRaises(ValueError):self.c.action('root','queue-reorder',{'ids':ids})
        self.c.action('root','queue-reorder',{'ids':['b','a']})
        self.assertEqual(self.b.rpc.calls[-1][1]['queuedSubmissionIds'],['b','a'])
    def test_queue_delete_reconciles_bridge_record(self):
        self.b.queued_sends['client']={'threadId':'root','nativeId':'q'}
        self.c.action('root','queue-delete',{'submissionId':'q'})
        self.assertNotIn('client',self.b.queued_sends);self.assertIn('client',self.b.cancelled_sends)
    def test_project_delete_has_no_filesystem_delete(self):
        f=self.root/'keep';f.write_text('keep')
        with self.assertRaises(ValueError):self.c.action('root','project-delete',{'projectId':'p'})
        self.c.action('root','project-delete',{'projectId':'p','confirmed':True});self.assertTrue(f.exists())
    def test_project_create_requires_existing_absolute_directory(self):
        with self.assertRaises(ValueError):self.c.action('root','project-create',{'path':'relative','name':'x','messageId':'id'})
        self.c.action('root','project-create',{'path':str(self.root),'name':'x','messageId':'id'})
        self.assertEqual(self.b.rpc.calls[-1][0],'project/create')
    def test_folder_stays_inside_workspace(self):
        with self.assertRaises(ValueError):self.c.action('root','folder-create',{'path':'../escape'})
        self.c.action('root','folder-create',{'path':'new'});self.assertTrue((self.root/'new').is_dir())
    def test_review_requires_confirmation(self):
        with self.assertRaises(ValueError):self.c.action('root','review',{})
        self.c.action('root','review',{'confirmed':True});self.assertEqual(self.b.rpc.calls[-1][0],'review/start')
    def test_search_has_cursor_and_limit(self):
        self.c.read('root','search',{'query':'test','cursor':'opaque'})
        self.assertEqual(self.b.rpc.calls[-1][1],{'threadId':'root','searchTerm':'test','cursor':'opaque','limit':30})
    def test_unknown_action_and_null_are_rejected(self):
        for action in ['spawn-agent',None]:
            with self.assertRaises(ValueError):self.c.action('root',action,{})

    def test_busy_message_uses_native_queue_and_retry_is_not_added_twice(self):
        def busy(*args):raise BusyError('active')
        self.b.send=busy
        result=Bridge.submit(self.b,'text',[],'client','root')
        self.assertTrue(result['queued']);self.assertEqual(self.b.queued_sends['client']['nativeId'],'native-id')
        self.assertEqual(sum(m=='thread/queue/add' for m,p in self.b.rpc.calls),1)
        Bridge.submit(self.b,'text',[],'client','root')
        self.assertEqual(sum(m=='thread/queue/add' for m,p in self.b.rpc.calls),1)
    def test_override_stays_in_bridge_queue_without_silent_loss(self):
        def busy(*args):raise BusyError('active')
        self.b.send=busy
        Bridge.submit(self.b,'text',[],'client','root','chosen-model','high')
        self.assertIsNone(self.b.queued_sends['client']['nativeId'])
        self.assertEqual(self.b.queued_sends['client']['model'],'chosen-model')
        self.assertNotIn('thread/queue/add',[m for m,p in self.b.rpc.calls])

    def test_fork_retry_uses_cached_operation_and_rejects_conflict(self):
        body={'turnId':'turn','operationId':'operation'}
        self.c.action('root','fork',body);self.c.action('root','fork',body)
        self.assertEqual(sum(m=='thread/fork' for m,p in self.b.rpc.calls),1)
        with self.assertRaises(ValueError):self.c.action('root','fork',{'turnId':'another','operationId':'operation'})

class GitControlsTests(ControlsTests):
    # Reuse controls cases with a real isolated repository too.
    def setUp(self):
        super().setUp()
        self.run_git('init','-q');self.run_git('config','user.name','Synthetic');self.run_git('config','user.email','fixture@example.invalid')
    def run_git(self,*args):
        return subprocess.check_output(['git','-C',str(self.root),*args],stderr=subprocess.DEVNULL).decode()
    def test_unborn_repo_status(self):
        result=self.c.git('root','status',{});self.assertEqual(result['status'],'')
    def test_stage_commit_and_dirty_switch_guard(self):
        (self.root/'file.txt').write_text('synthetic')
        with self.assertRaises(ValueError):self.c.git('root','stage',{})
        self.c.git('root','stage',{'confirmed':True});self.c.git('root','commit',{'confirmed':True,'message':'Synthetic fixture'})
        self.assertIn('Synthetic fixture',self.run_git('log','-1','--pretty=%s'))
        self.run_git('branch','test-branch');(self.root/'file.txt').write_text('changed')
        with self.assertRaises(ValueError):self.c.git('root','switch',{'confirmed':True,'branch':'test-branch'})
        self.assertEqual((self.root/'file.txt').read_text(),'changed')
    def test_argument_injection_rejected(self):
        with self.assertRaises(ValueError):self.c.git('root','switch',{'confirmed':True,'branch':'--orphan=bad'})
    def test_parent_repo_refused(self):
        nested=self.root/'nested';nested.mkdir();self.b.root=nested
        with self.assertRaises(ValueError):self.c.git('root','status',{})

    def test_clean_switch_and_worktree_create(self):
        (self.root/'file.txt').write_text('synthetic')
        self.c.git('root','stage',{'confirmed':True});self.c.git('root','commit',{'confirmed':True,'message':'fixture'})
        self.run_git('branch','test-branch');self.c.git('root','switch',{'confirmed':True,'branch':'test-branch'})
        self.assertEqual(self.run_git('branch','--show-current').strip(),'test-branch')
        self.run_git('branch','worktree-branch')
        target=self.root.parent/(self.root.name+'-fixture')
        self.addCleanup(lambda: self.run_git('worktree','remove','--',str(target)) if target.exists() else None)
        self.c.git('root','worktree',{'confirmed':True,'branch':'worktree-branch','name':'fixture'})
        self.assertTrue((target/'file.txt').exists())
