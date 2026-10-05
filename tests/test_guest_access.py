import concurrent.futures
from pathlib import Path
import secrets
import tempfile
import unittest

from bridge.guest_access import AccessError, GuestAccess, quota_rule


def sample(five=80, week=60, first=10000, second=20000):
    return {'fiveHours': {'remainingPercent': five, 'resetsAt': first},
            'week': {'remainingPercent': week, 'resetsAt': second}}


def rule(mode='fixed', basis='full', amount=15):
    return {'mode':mode, 'basis':basis, 'amount':amount}


class GuestsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'guests.sqlite3'
        self.store=GuestAccess(self.path)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def invite(self, quotas=None):
        data={'operationId':secrets.token_hex(16), 'secret':secrets.token_urlsafe(32),
              'name':'Тестовый гость', 'quotas':quotas or {'fiveHours':{'mode':'unlimited'}, 'week':rule()}}
        return data,self.store.invite(data,sample())

    def action(self, guest, action, **kwargs):
        return self.store.change({'operationId':secrets.token_hex(16), 'guestId':guest,'action':action,**kwargs},sample())

    def test_invite_retry_stores_only_hashes(self):
        data,created=self.invite()
        self.assertEqual(created,self.store.invite(data,sample()))
        self.assertEqual(len(self.store.list()['guests']),1)
        self.assertNotIn(data['secret'], self.path.read_bytes().decode(errors='ignore'))
        changed={**data,'name':'Другое имя'}
        with self.assertRaises(ValueError): self.store.invite(changed,sample())

    def test_account_switch_revokes_guests_without_storing_identity(self):
        self.store.bind_account('synthetic-account-one')
        data,created=self.invite();token=secrets.token_urlsafe(32)
        self.store.redeem(data['secret'],'device-fixture',token)
        self.assertFalse(self.store.bind_account('synthetic-account-one'))
        self.assertEqual(self.store.authenticate(token),created['guestId'])
        self.assertTrue(self.store.bind_account('synthetic-account-two'))
        with self.assertRaises(AccessError):self.store.authenticate(token)
        self.assertNotIn('synthetic-account-two',self.path.read_bytes().decode(errors='ignore'))

    def test_redeem_lost_response_and_replay(self):
        data,created=self.invite()
        token=secrets.token_urlsafe(32)
        result=self.store.redeem(data['secret'],'device-001',token)
        self.assertEqual(result,self.store.redeem(data['secret'],'device-001',token))
        self.assertEqual(self.store.authenticate(token),created['guestId'])
        with self.assertRaises(AccessError): self.store.redeem(data['secret'],'device-002',secrets.token_urlsafe(32))
        self.action(created['guestId'],'revoke')
        with self.assertRaises(AccessError): self.store.authenticate(token)
        with self.assertRaises(AccessError): self.store.redeem(data['secret'],'device-001',token)

    def test_consumed_invite_retry_after_expiry_only_same_valid_session(self):
        data,_=self.invite();token=secrets.token_urlsafe(32)
        first=self.store.redeem(data['secret'],'device-fixture',token)
        self.store.db.execute('UPDATE invitations SET expires=0')
        self.assertEqual(first,self.store.redeem(data['secret'],'device-fixture',token))
        with self.assertRaises(AccessError):self.store.redeem(data['secret'],'other-device',token)
        self.store.db.execute('UPDATE sessions SET expires=0')
        with self.assertRaises(AccessError):self.store.redeem(data['secret'],'device-fixture',token)

    def test_invite_consumed_once_concurrently(self):
        data,_=self.invite()
        def redeem(n):
            try:
                self.store.redeem(data['secret'],f'device-{n:03}',secrets.token_urlsafe(32))
                return True
            except AccessError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(redeem,range(8))),1)

    def test_fixed_survives_reset_and_unlimited_independent(self):
        _,created=self.invite()
        guest=created['guestId']
        self.store.begin(guest,'operation-1',sample())
        self.store.finish('operation-1',sample(five=70,week=50))
        self.store.begin(guest,'operation-2',sample(first=30000,second=40000))
        view=self.store.view(guest)
        self.assertEqual(view['quotas']['week']['spent'],10)
        self.assertEqual(view['quotas']['week']['remaining'],5)
        self.assertIsNone(view['quotas']['fiveHours']['remaining'])
        self.store.finish('operation-2',sample(week=54,first=30000,second=40000))
        with self.assertRaises(AccessError): self.store.begin(guest,'operation-3',sample(first=30000,second=40000))

    def test_idle_display_renews_only_after_running_meter_finishes(self):
        _,created=self.invite({'fiveHours':rule('renewing','remaining',10),'week':rule()})
        guest=created['guestId']
        self.store.begin(guest,'display-fixture',sample())
        self.store.checkpoint('display-fixture',sample(five=77,week=58))
        self.assertFalse(self.store.synchronize_idle(guest,sample(first=30000)))
        self.assertEqual(self.store.view(guest)['quotas']['fiveHours']['spent'],3)
        self.store.finish('display-fixture',sample(five=77,week=58))
        self.assertTrue(self.store.synchronize_idle(guest,sample(five=90,first=30000)))
        budgets=self.store.view(guest)['quotas']
        self.assertEqual(budgets['fiveHours']['spent'],0)
        self.assertEqual(budgets['fiveHours']['allocated'],9)
        self.assertEqual(budgets['week']['spent'],2)

    def test_remaining_and_renewing_windows_independent(self):
        _,created=self.invite({'fiveHours':rule('renewing','remaining',10),'week':rule()})
        guest=created['guestId']
        self.assertEqual(self.store.view(guest)['quotas']['fiveHours']['allocated'],8)
        self.store.begin(guest,'operation-1',sample())
        self.store.finish('operation-1',sample(five=77,week=58))
        self.store.begin(guest,'operation-2',sample(five=90,week=58,first=30000))
        view=self.store.view(guest)['quotas']
        self.assertEqual(view['fiveHours']['allocated'],9)
        self.assertEqual(view['fiveHours']['spent'],0)
        self.assertEqual(view['week']['spent'],2)

    def test_configure_preserves_spent_reallocate_explicit(self):
        _,created=self.invite()
        guest=created['guestId']
        self.store.begin(guest,'operation-1',sample())
        self.store.finish('operation-1',sample(week=55))
        self.action(guest,'configure',quotas={'week':rule(amount=20)})
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],5)
        self.action(guest,'reallocate',quotas={'week':rule(amount=20)})
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],0)

    def test_reallocation_during_measurement_preserves_budget(self):
        _,created=self.invite();guest=created['guestId']
        self.store.begin(guest,'active-reallocation',sample())
        self.store.checkpoint('active-reallocation',sample(week=58))
        with self.assertRaises(ValueError):self.action(guest,'reallocate',quotas={'week':rule(amount=20)})
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],2)
        self.store.checkpoint('active-reallocation',sample(),interference=True)
        with self.assertRaises(ValueError):self.action(guest,'reallocate',quotas={'week':rule(amount=20)})

    def test_lane_and_external_activity_blocked(self):
        _,first=self.invite()
        _,second=self.invite()
        with self.assertRaises(AccessError): self.store.begin(first['guestId'],'operation-1',sample(),external_active=True)
        self.store.begin(first['guestId'],'operation-1',sample())
        self.assertTrue(self.store.begin(first['guestId'],'operation-1',sample())['replayed'])
        with self.assertRaises(AccessError): self.store.begin(second['guestId'],'operation-2',sample())
        with self.assertRaises(AccessError): self.store.begin(second['guestId'],'operation-1',sample())

    def test_uncertain_expense_does_not_charge_owner_usage(self):
        _,created=self.invite()
        guest=created['guestId']
        self.store.begin(guest,'operation-1',sample())
        self.assertEqual(self.store.finish('operation-1',sample(week=40),True)['state'],'uncertain')
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],0)
        self.assertEqual(self.store.finish('operation-1',sample(week=30))['state'],'uncertain')
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],0)
        with self.assertRaises(AccessError): self.store.begin(guest,'operation-2',sample())

    def test_finish_once_and_persistence(self):
        _,created=self.invite()
        guest=created['guestId']
        self.store.begin(guest,'operation-1',sample())
        self.store.finish('operation-1',sample(week=58))
        self.store.finish('operation-1',sample(week=40))
        self.store.db.close()
        self.store=GuestAccess(self.path)
        self.assertEqual(self.store.view(guest)['quotas']['week']['spent'],2)

    def test_permissions_and_expiry(self):
        data,created=self.invite()
        guest=created['guestId']
        self.action(guest,'grant',kind='thread',resourceId='synthetic-chat',right='view')
        self.assertEqual(self.store.view(guest)['grants'][0]['right'],'view')
        self.action(guest,'unshare',kind='thread',resourceId='synthetic-chat')
        self.assertEqual(self.store.view(guest)['grants'],[])
        self.store.db.execute('UPDATE invitations SET expires=0')
        with self.assertRaises(AccessError): self.store.redeem(data['secret'],'device-001',secrets.token_urlsafe(32))

    def test_owner_rotation_revokes_old_sessions(self):
        self.store.bind_owner('owner-1')
        data,_=self.invite()
        token=secrets.token_urlsafe(32)
        self.store.redeem(data['secret'],'device-001',token)
        self.store.bind_owner('owner-1')
        self.store.authenticate(token)
        self.store.bind_owner('owner-2')
        with self.assertRaises(AccessError):self.store.authenticate(token)
        with self.assertRaises(AccessError):self.store.redeem(data['secret'],'device-001',token)
        self.assertEqual(self.store.list()['guests'],[])

    def test_idempotent_invite_does_not_need_new_snapshot(self):
        data,created=self.invite({'fiveHours':rule(),'week':rule()})
        def unavailable():raise RuntimeError('provider unavailable')
        self.assertEqual(created,self.store.invite(data,unavailable))

    def test_invalid_rules(self):
        for amount in (True,0,-1,101,float('nan'),float('inf'),'15'):
            with self.assertRaises(ValueError): quota_rule(rule(amount=amount))
        _,created=self.invite()
        with self.assertRaises(AccessError): self.store.begin(created['guestId'],'operation-1',{})


if __name__=='__main__': unittest.main()
