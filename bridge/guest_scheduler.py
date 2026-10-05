"""One durable guest execution lane. Host integration must supply an idle gate.

Runner is trusted code, never a guest-controlled RPC proxy. A runner must return
only after its processes stop, and call checkpoint during execution. Ambiguous
launches remain blocked and are never retried automatically.
"""
import threading
from contextlib import nullcontext


class GuestScheduler:
    def __init__(self, access, jobs, *, snapshot, owner_idle, runner, launch_guard=nullcontext, can_launch=None, on_reserved=lambda:None):
        self.access=access
        self.jobs=jobs
        self.snapshot=snapshot
        self.owner_idle=owner_idle
        self.runner=runner
        self.launch_guard=launch_guard
        self.can_launch=can_launch or owner_idle
        self.on_reserved=on_reserved
        self.lock=threading.Lock()
        self.jobs.recover()

    def step(self):
        if not self.lock.acquire(blocking=False):return None
        task=None
        reserved=False
        try:
            with self.launch_guard():
                if not self.can_launch():return None
                task=self.jobs.claim()
                if task is None:return None
                op=task['operationId']
                if not self.can_launch():
                    self.jobs.update(op,'queued')
                    return None
                try:
                    self.access.begin(task['guestId'],op,self.snapshot())
                    reserved=True
                    self.on_reserved()
                except Exception:
                    self.jobs.defer(op)
                    raise
            def checkpoint():
                current=self.jobs.get(task['guestId'],op)
                measurement=self.access.checkpoint(op,self.snapshot(),interference=not self.owner_idle())
                reason=('cancelled' if current['state']=='cancel_requested' else 'meter_uncertain' if measurement['state']=='uncertain' else 'quota_limit' if measurement.get('quotaExhausted',False) else '')
                return {'stop':bool(reason),'stopReason':reason,'measurement':measurement}
            # Cancellation can arrive between durable claim and launch.
            if self.jobs.get(task['guestId'],op)['state']=='cancel_requested':
                self.access.finish(op,self.snapshot())
                self.jobs.update(op,'cancelled')
                return self.jobs.get(task['guestId'],op)
            self.jobs.update(op,'running')
            result=self.runner(task,checkpoint)
            measurement=self.access.finish(op,self.snapshot(),interference=not self.owner_idle())
            current=self.jobs.get(task['guestId'],op)
            state='uncertain' if measurement['state']=='uncertain' else (
                'cancelled' if current['state']=='cancel_requested' or (isinstance(result,dict) and result.get('status')=='interrupted') else (
                    'failed' if isinstance(result,dict) and result.get('status')=='failed' else 'completed'))
            self.jobs.update(op,state,result={'output':result,'measurement':measurement})
            return self.jobs.get(task['guestId'],op)
        except Exception:
            if task is not None and reserved:
                # A lost launch/completion reply cannot prove whether work ran.
                try:self.access.checkpoint(task['operationId'],self.snapshot(),interference=True)
                finally:self.jobs.update(task['operationId'],'uncertain',reason='execution_outcome_unknown')
            raise
        finally:
            self.lock.release()
