from pathlib import Path
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import base64
from bridge.guest_access import GuestAccess,AccessError
from bridge.guest_resources import GuestResources,read_regular
from tests.test_guest_access import sample


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        (self.source/'app.txt').write_text('base')
        (self.source/'.env').write_text('synthetic excluded secret')
        (self.source/'outside').symlink_to(self.root/'not-shared')
        self.access=GuestAccess(self.root/'guest.sqlite3')
        secret=secrets.token_urlsafe(32)
        self.guest=self.access.invite({'operationId':'resource-invite-fixture','secret':secret,'name':'Fixture',
            'quotas':{'fiveHours':{'mode':'unlimited'},'week':{'mode':'unlimited'}}},sample())['guestId']
        self.access.redeem(secret,'fixture-device',secrets.token_urlsafe(32))
        bridge=SimpleNamespace(guests=self.access,state_file=self.root/'state.json',workspace_path=lambda thread:(self.source,self.source),
            projects=lambda:{'projects':[{'id':'fixture-project','cwd':str(self.source),'threads':[{'id':'fixture-thread'}]}]},history=lambda thread,before:{'turns':[{'text':'Synthetic public message'}]})
        self.resources=GuestResources(bridge)

    def tearDown(self):self.access.db.close();self.temp.cleanup()

    def grant(self,right='work',action='grant'):
        self.access.change({'operationId':secrets.token_hex(16),'guestId':self.guest,'action':action,'kind':'thread','resourceId':'fixture-thread','right':right},sample())

    def copy(self):return self.resources.prepare_copy(self.guest,{'operationId':'copy-fixture-001','kind':'thread','resourceId':'fixture-thread'})['scopeId']

    def test_acl_copy_isolation_and_revoke(self):
        with self.assertRaises(AccessError):self.resources.read(self.guest,'thread','fixture-thread')
        self.grant('view')
        self.assertTrue(self.resources.history(self.guest,'fixture-thread')['turns'])
        with self.assertRaises(AccessError):self.copy()
        self.grant();scope=self.copy();copy=self.resources.copy_workspace(self.guest,scope)
        self.assertFalse((copy/'.env').exists());self.assertFalse((copy/'outside').exists())
        (copy/'app.txt').write_text('guest changed')
        self.assertEqual((self.source/'app.txt').read_text(),'base')
        self.assertEqual(self.copy(),scope)
        self.grant(action='unshare')
        with self.assertRaises(AccessError):self.resources.copy_workspace(self.guest,scope)

    def test_review_requires_exact_content_and_owner_base(self):
        self.grant();scope=self.copy();copy=self.resources.copy_workspace(self.guest,scope)
        (copy/'app.txt').write_text('guest changed')
        review=self.resources.review(scope)
        self.assertEqual(len(review['changes']),1)
        preview=self.resources.review(scope,'app.txt')
        with self.assertRaises(ValueError):self.resources.review(scope,'app.txt',True,'wrong-hash')
        self.resources.review(scope,'app.txt',True,preview['guestHash'])
        self.assertEqual((self.source/'app.txt').read_text(),'guest changed')
        (copy/'app.txt').write_text('second change');(self.source/'app.txt').write_text('owner changed')
        preview=self.resources.review(scope,'app.txt')
        with self.assertRaises(ValueError):self.resources.review(scope,'app.txt',True,preview['guestHash'])
        self.assertEqual((self.source/'app.txt').read_text(),'owner changed')

    def test_traversal_symlink_and_deleted_file_blocked(self):
        self.grant();scope=self.copy();copy=self.resources.copy_workspace(self.guest,scope)
        with self.assertRaises(ValueError):read_regular(copy,'../source/app.txt')
        (copy/'leak').symlink_to(self.source/'app.txt')
        with self.assertRaises(ValueError):read_regular(copy,'leak')
        (copy/'app.txt').unlink()
        with self.assertRaises(ValueError):self.resources.review(scope,'app.txt',True,None)

    def test_shared_reads_exclude_credentials_and_internal_folders(self):
        self.grant('view')
        (self.source/'.codex').mkdir();(self.source/'.codex'/'config.toml').write_text('private fixture')
        names=[row['name'] for row in self.resources.read(self.guest,'thread','fixture-thread')['entries']]
        self.assertNotIn('.env',names);self.assertNotIn('.codex',names)
        for path in ('.env','.codex/config.toml','auth.json','secret.pem'):
            with self.assertRaises(AccessError):self.resources.read(self.guest,'thread','fixture-thread',path,True)

    def test_shared_blob_and_absolute_link_respect_acl(self):
        self.grant('view')
        result=self.resources.read(self.guest,'thread','fixture-thread',str(self.source/'app.txt'),blob=True)
        self.assertEqual(base64.b64decode(result['dataBase64']),b'base')
        with self.assertRaises(AccessError):self.resources.read(self.guest,'thread','fixture-thread',str(self.root/'private.txt'),blob=True)
        with self.assertRaises(AccessError):self.resources.read(self.guest,'thread','fixture-thread',str(self.source/'.env'),blob=True)

    def test_storage_budget_blocks_upload_without_reading_broker(self):
        root=self.resources.own_root(self.guest)
        (root/'one.txt').write_bytes(b'x'*64)
        broker=root.parent/'broker';broker.mkdir();(broker/'auth.json').write_bytes(b'x'*1000)
        self.assertEqual(self.resources.storage_status(self.guest)['bytes'],64)
        with patch('bridge.guest_resources.MAX_GUEST_STORAGE',70):
            with self.assertRaises(ValueError):self.resources.own_write(self.guest,{'path':'two.txt','dataBase64':base64.b64encode(b'y'*10).decode()})
        self.assertFalse((root/'two.txt').exists())
        (root/'ignored').symlink_to(broker/'auth.json')
        self.assertEqual(self.resources.storage_status(self.guest)['bytes'],64)

    def test_upload_and_download_are_guest_owned_and_no_follow(self):
        import base64
        self.resources.own_write(self.guest,{'path':'hello.txt','dataBase64':base64.b64encode(b'guest content').decode()})
        result=self.resources.own_read(self.guest,'default','hello.txt',True)
        self.assertEqual(base64.b64decode(result['dataBase64']),b'guest content')
        own=self.resources.own_root(self.guest)
        (own/'escape').symlink_to(self.source)
        with self.assertRaises(ValueError):self.resources.own_write(self.guest,{'path':'escape/app.txt','dataBase64':'eA=='})
        self.assertEqual((self.source/'app.txt').read_text(),'base')

    def test_copy_excludes_secret_directories(self):
        for directory in ('.env.production','auth.json','secret.key'):
            target=self.source/directory;target.mkdir();(target/'nested.txt').write_text('secret fixture')
        self.grant();scope=self.copy();root=self.resources.copy_workspace(self.guest,scope)
        for directory in ('.env.production','auth.json','secret.key'):
            self.assertFalse((root/directory).exists())

    def test_revocation_during_preview_and_history_denies_response(self):
        self.grant('view')
        original=read_regular
        def revoke_read(*args,**kwargs):
            result=original(*args,**kwargs);self.grant(action='unshare');return result
        with patch('bridge.guest_resources.read_regular',side_effect=revoke_read):
            with self.assertRaises(AccessError):self.resources.read(self.guest,'thread','fixture-thread','app.txt',True)
        self.grant('view')
        def revoke_history(*args):
            self.grant(action='unshare');return {'turns':[{'text':'must not escape'}]}
        self.resources.bridge.history=revoke_history
        with self.assertRaises(AccessError):self.resources.history(self.guest,'fixture-thread')

    def test_revocation_during_copy_read_and_upload_denies_response(self):
        self.grant();scope=self.copy();original=read_regular
        def revoke_read(*args,**kwargs):
            result=original(*args,**kwargs);self.grant(action='unshare');return result
        with patch('bridge.guest_resources.read_regular',side_effect=revoke_read):
            with self.assertRaises(AccessError):self.resources.own_read(self.guest,scope,'app.txt',True)
        self.grant()
        original_check=self.resources.storage_status
        def revoke_storage(*args,**kwargs):
            result=original_check(*args,**kwargs);self.grant(action='unshare');return result
        with patch.object(self.resources,'storage_status',side_effect=revoke_storage):
            with self.assertRaises(AccessError):self.resources.own_write(self.guest,{'scopeId':scope,'path':'new.txt','dataBase64':'eA=='})
        self.assertFalse((self.root/'guest-runtime'/self.guest/'copies'/scope/'new.txt').exists())

    def test_new_nested_file_review_and_apply(self):
        self.grant();scope=self.copy();root=self.resources.copy_workspace(self.guest,scope)
        (root/'new').mkdir();(root/'new'/'file.txt').write_text('new file')
        preview=self.resources.review(scope,'new/file.txt')
        self.resources.review(scope,'new/file.txt',True,preview['guestHash'])
        self.assertEqual((self.source/'new'/'file.txt').read_text(),'new file')

    def test_copy_respects_aggregate_storage_limit(self):
        self.grant();root=self.resources.own_root(self.guest);(root/'existing.txt').write_bytes(b'x'*64)
        with patch('bridge.guest_resources.MAX_GUEST_STORAGE',65):
            with self.assertRaises(ValueError):self.copy()
        self.assertEqual(self.access.db.execute('SELECT count(*) FROM guest_copies').fetchone()[0],0)

    def test_upload_can_replace_file_at_storage_capacity(self):
        root=self.resources.own_root(self.guest);(root/'existing.txt').write_bytes(b'x'*64)
        with patch('bridge.guest_resources.MAX_GUEST_STORAGE',64):
            self.resources.own_write(self.guest,{'path':'existing.txt','dataBase64':base64.b64encode(b'y'*64).decode()})
        self.assertEqual((root/'existing.txt').read_bytes(),b'y'*64)
        self.assertFalse(any(p.name.startswith('rv-upload-') for p in root.iterdir()))

    def test_review_preserves_existing_file_permissions(self):
        self.grant();scope=self.copy();root=self.resources.copy_workspace(self.guest,scope)
        (self.source/'app.txt').chmod(0o755);(root/'app.txt').write_text('changed script')
        preview=self.resources.review(scope,'app.txt')
        self.resources.review(scope,'app.txt',True,preview['guestHash'])
        self.assertEqual((self.source/'app.txt').stat().st_mode & 0o777,0o755)

    def test_large_utf8_preview_is_bounded_and_marked_truncated(self):
        self.grant('view');(self.source/'large.txt').write_text('я'*100000)
        preview=self.resources.read(self.guest,'thread','fixture-thread','large.txt',True)
        self.assertTrue(preview['previewable']);self.assertTrue(preview['truncated'])
        self.assertEqual(preview['text'],'я'*65536)
        with self.assertRaises(ValueError):read_regular(self.source,'large.txt',131072)

    def test_invalid_resource_and_scope_are_validation_errors(self):
        for kind,resource in (([],[]),('wrong','fixture-thread'),('thread',None)):
            with self.assertRaises(ValueError):self.resources.right(self.guest,kind,resource)
        with self.assertRaises(ValueError):self.resources.own_root(self.guest,[])

    def test_owner_lists_copies_without_any_guest_tasks_and_paginates(self):
        self.grant();scope=self.copy()
        self.assertEqual(self.resources.owner_copies(self.guest)['copies'][0]['id'],scope)
        for number in range(105):
            self.access.db.execute('INSERT INTO guest_copies VALUES(?,?,?,?,?,?)',
                (f'{number:032x}',self.guest,'thread','fixture-thread',str(self.source),'{}'))
        first=self.resources.owner_copies(self.guest)
        second=self.resources.owner_copies(self.guest,first['nextCursor'])
        self.assertEqual(len(first['copies']),100);self.assertEqual(len(second['copies']),6)
        self.assertEqual(len({c['id'] for c in first['copies']+second['copies']}),106)
        with self.assertRaises(ValueError):self.resources.owner_copies(self.guest,'foreign-copy')
