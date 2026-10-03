"""Completion scenarios use temporary files and inert Desktop replies."""
from pathlib import Path
import tempfile
import threading
import unittest
from bridge.workspace_listing import listing
from bridge.server import Bridge


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def test_all_pages_and_search_beyond_first_page(self):
        for n in range(421): (self.root/f'file-{n:04d}.txt').write_text('fixture')
        a=listing(self.root,self.root,'');b=listing(self.root,self.root,'',cursor=a['nextCursor']);c=listing(self.root,self.root,'',cursor=b['nextCursor'])
        self.assertEqual(len(a['entries'])+len(b['entries'])+len(c['entries']),421)
        self.assertIsNone(c['nextCursor'])
        self.assertEqual(listing(self.root,self.root,'','0420')['entries'][0]['name'],'file-0420.txt')
        (self.root/'new-file').touch()
        with self.assertRaisesRegex(ValueError,'изменился'):listing(self.root,self.root,'',cursor=a['nextCursor'])
    def test_hidden_service_and_link_policy(self):
        (self.root/'.env').write_text('synthetic');(self.root/'build').mkdir();(self.root/'target').touch()
        (self.root/'internal').symlink_to(self.root/'target');(self.root/'external').symlink_to(self.root.parent)
        default=listing(self.root,self.root,'')['entries']
        self.assertNotIn('.env',[e['name'] for e in default]);self.assertNotIn('build',[e['name'] for e in default])
        expanded=listing(self.root,self.root,'',hidden=True,service=True)['entries']
        self.assertIn('.env',[e['name'] for e in expanded]);self.assertIn('build',[e['name'] for e in expanded])
        self.assertFalse(next(e for e in default if e['name']=='external')['available'])
        self.assertTrue(next(e for e in default if e['name']=='internal')['available'])
    def test_changed_filter_invalidates_cursor(self):
        for n in range(201): (self.root/str(n)).touch()
        page=listing(self.root,self.root,'')
        with self.assertRaises(ValueError):listing(self.root,self.root,'',hidden=True,cursor=page['nextCursor'])
    def test_malformed_cursor_is_validation_error(self):
        for cursor in ('!', 'a', '[]', 'x'*1025):
            with self.subTest(cursor=cursor[:12]), self.assertRaises(ValueError):
                listing(self.root,self.root,'',cursor=cursor)



class QueueTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge.__new__(Bridge);self.b.queue_lock=threading.Lock();self.b.state_lock=threading.RLock()
        self.b.queued_sends={'client':{'threadId':'root','nativeId':'native','files':[],'text':'Original'}}
        self.b.sent_messages={};self.b.pending={};self.saved=0
        self.b._save_state=lambda: setattr(self,'saved',self.saved+1)
        self.queue=[];self.turns=[]
        class Rpc:
            def call(inner,method,params):
                if method=='thread/queue/list':return {'data':self.queue,'nextCursor':None}
                if method=='thread/read':return {'thread':{'turns':self.turns}}
                raise AssertionError('No mutation allowed: '+method)
        self.rpc=Rpc()
    def test_missing_is_unknown_never_resent_and_delivery_reconciles(self):
        self.b.reconcile_native_queue('root',self.rpc)
        self.assertEqual(self.b.queued_sends['client']['queueState'],'checking')
        self.assertNotIn('client',self.b.sent_messages)
        self.b.reconcile_native_queue('root',self.rpc);self.assertEqual(self.saved,1)
        self.turns=[{'id':'accepted','items':[{'type':'userMessage','clientId':'client'}]}]
        self.b.reconcile_native_queue('root',self.rpc)
        self.assertFalse(self.b.queued_sends);self.assertEqual(self.b.sent_messages['client']['turnId'],'accepted')
    def test_desktop_edits_and_other_thread_do_not_leak(self):
        self.queue=[{'id':'native','input':[{'type':'text','text':'Changed on Desktop'}]}]
        self.b.reconcile_native_queue('other',self.rpc)
        self.assertEqual(self.b.queued_sends['client']['text'],'Original')
        self.b.reconcile_native_queue('root',self.rpc)
        self.assertEqual(self.b.queued_sends['client']['text'],'Changed on Desktop')
        self.assertEqual(self.b.queued_sends['client']['queueState'],'queued')
