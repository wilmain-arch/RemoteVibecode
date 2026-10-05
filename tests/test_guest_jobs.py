import secrets
import tempfile
import unittest
from pathlib import Path
from bridge.guest_access import GuestAccess, AccessError
from bridge.guest_jobs import GuestJobs
from bridge.guest_scheduler import GuestScheduler
from tests.test_guest_access import sample


class GuestJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.access=GuestAccess(Path(self.temp.name)/'store.sqlite3')
        self.jobs=GuestJobs(self.access)
        self.guest=self.create_guest()

    def create_guest(self):
        secret=secrets.token_urlsafe(32)
        guest=self.access.invite({'operationId':secrets.token_hex(16),'secret':secret,'name':'Fixture',
            'quotas':{'fiveHours':{'mode':'unlimited'},'week':{'mode':'fixed','basis':'full','amount':15}}},sample())['guestId']
        self.access.redeem(secret,secrets.token_hex(16),secrets.token_urlsafe(32))
        return guest

    def tearDown(self):
        self.access.db.close()
        self.temp.cleanup()

    def enqueue(self,op='operation-123'):
        return self.jobs.enqueue(self.guest,{'operationId':op,'text':'Synthetic task'})

    def test_retry_and_ownership(self):
        first=self.enqueue()
        self.assertEqual(first,self.enqueue())
        with self.assertRaises(AccessError):
            self.jobs.enqueue(self.create_guest(),{'operationId':'operation-123','text':'Synthetic task'})
        with self.assertRaises(AccessError):
            self.jobs.enqueue(self.guest,{'operationId':'operation-123','text':'Changed'})

    def test_serial_claim_cancel_and_revoke_race(self):
        self.enqueue();self.enqueue('operation-456')
        self.assertEqual(self.jobs.claim()['operationId'],'operation-123')
        self.assertIsNone(self.jobs.claim())
        self.jobs.revoke(self.guest)
        self.jobs.update('operation-123','running',turn='turn-1')
        self.assertEqual(self.jobs.get(self.guest,'operation-123')['state'],'cancel_requested')
        self.assertEqual(self.jobs.get(self.guest,'operation-456')['state'],'cancelled')

    def test_restart_never_replays_dispatched_task(self):
        self.enqueue();self.jobs.claim();self.jobs.recover()
        self.assertEqual(self.enqueue()['state'],'uncertain')
        self.assertIsNone(self.jobs.claim())

    def test_deferred_budget_does_not_block_other_guests(self):
        self.enqueue()
        other=self.create_guest()
        self.jobs.enqueue(other,{'operationId':'other-guest-task','text':'Synthetic task'})
        self.access.begin(self.guest,'consume-budget-fixture',sample())
        self.access.finish('consume-budget-fixture',sample(week=45))
        scheduler=GuestScheduler(self.access,self.jobs,snapshot=sample,owner_idle=lambda:True,runner=lambda task,checkpoint:{'status':'completed'})
        with self.assertRaises(AccessError):scheduler.step()
        self.assertEqual(scheduler.step()['operationId'],'other-guest-task')
        self.assertEqual(self.jobs.get(self.guest,'operation-123')['state'],'queued')

    def test_history_pagination_and_cursor_ownership(self):
        for number in range(105):
            op=f'history-task-{number:04d}'
            self.jobs.enqueue(self.guest,{'operationId':op,'text':'Synthetic task'})
            self.jobs.update(op,'completed')
        first=self.jobs.list(self.guest)
        self.assertEqual(len(first['tasks']),100)
        second=self.jobs.list(self.guest,first['nextCursor'])
        self.assertEqual(len(second['tasks']),5)
        self.assertIsNone(second['nextCursor'])
        self.assertEqual(len({t['operationId'] for t in first['tasks']+second['tasks']}),105)
        with self.assertRaises(ValueError):self.jobs.list(self.create_guest(),first['nextCursor'])

    def test_cancel_queued(self):
        self.enqueue()
        self.assertEqual(self.jobs.cancel(self.guest,'operation-123')['state'],'cancelled')
        self.assertIsNone(self.jobs.claim())

    def test_checkpoint_does_not_double_charge(self):
        self.access.begin(self.guest,'operation-123',sample())
        self.assertFalse(self.access.checkpoint('operation-123',sample(week=55))['quotaExhausted'])
        self.access.checkpoint('operation-123',sample(week=55))
        result=self.access.finish('operation-123',sample(week=45))
        self.assertTrue(result['quotaExhausted'])
        self.assertEqual(result['usage']['week'],15)
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],15)
        self.access.finish('operation-123',sample(week=40))
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],15)

    def test_interference_blocks_new_launch(self):
        self.access.begin(self.guest,'operation-123',sample())
        self.assertEqual(self.access.checkpoint('operation-123',sample(week=55),interference=True)['state'],'uncertain')
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],0)
        with self.assertRaises(AccessError):self.access.begin(self.guest,'operation-456',sample())

    def test_scheduler_owner_priority_and_completion(self):
        self.enqueue()
        idle=[False]
        meter=[sample()]
        def runner(task, checkpoint):
            meter[0]=sample(week=57)
            self.assertFalse(checkpoint()['stop'])
            return 'synthetic result'
        scheduler=GuestScheduler(self.access,self.jobs,snapshot=lambda:meter[0],owner_idle=lambda:idle[0],runner=runner)
        self.assertIsNone(scheduler.step())
        idle[0]=True
        self.assertEqual(scheduler.step()['state'],'completed')
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],3)

    def test_scheduler_unknown_launch_blocks_retry(self):
        self.enqueue()
        def runner(task, checkpoint):raise TimeoutError('Synthetic lost reply')
        scheduler=GuestScheduler(self.access,self.jobs,snapshot=sample,owner_idle=lambda:True,runner=runner)
        with self.assertRaises(TimeoutError):scheduler.step()
        self.assertEqual(self.enqueue()['state'],'uncertain')
        self.assertIsNone(scheduler.step())

    def test_owner_resolution_releases_lane_without_replaying(self):
        self.enqueue();self.jobs.claim()
        self.access.begin(self.guest,'operation-123',sample())
        self.access.checkpoint('operation-123',sample(week=57))
        self.access.checkpoint('operation-123',sample(week=56),interference=True)
        self.jobs.update('operation-123','uncertain')
        request={'operationId':'operation-123','confirmed':True,'additionalUsage':{'fiveHours':0,'week':2}}
        self.assertEqual(self.jobs.resolve(request)['state'],'failed')
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],5)
        with self.assertRaises(ValueError):self.jobs.resolve(request)
        self.enqueue('operation-456')
        self.assertEqual(self.jobs.claim()['operationId'],'operation-456')

    def test_owner_can_resolve_recovered_dispatch_without_meter(self):
        self.enqueue();self.jobs.claim();self.jobs.recover()
        result=self.jobs.resolve({'operationId':'operation-123','confirmed':True,'additionalUsage':{'fiveHours':0,'week':1}})
        self.assertEqual(result['state'],'failed')
        self.assertEqual(self.access.view(self.guest)['quotas']['week']['spent'],1)
        self.enqueue('operation-next')
        self.assertEqual(self.jobs.claim()['operationId'],'operation-next')

    def test_owner_list_keeps_old_uncertain_task_visible(self):
        self.enqueue();self.jobs.claim();self.jobs.recover()
        other=self.create_guest()
        for number in range(105):
            op=f'owner-history-{number:04d}'
            self.jobs.enqueue(other,{'operationId':op,'text':'Synthetic task'})
            self.jobs.update(op,'completed')
        self.assertEqual(self.jobs.owner_list()['tasks'][0]['operationId'],'operation-123')

    def test_requeue_cannot_resurrect_cancelled_task(self):
        self.enqueue();self.jobs.claim();self.jobs.cancel(self.guest,'operation-123')
        self.jobs.update('operation-123','queued')
        self.assertEqual(self.jobs.get(self.guest,'operation-123')['state'],'cancel_requested')
