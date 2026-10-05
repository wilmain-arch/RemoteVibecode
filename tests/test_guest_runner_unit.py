"""Runner boundaries with synthetic RPC only: no model or account calls."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from bridge.guest_runner import GuestRunner
from bridge.server import RpcError


class RunnerBoundaryTests(unittest.TestCase):
    def test_stop_before_launch_does_not_open_executor_or_rpc(self):
        runner=GuestRunner(workspace=Path('/synthetic'),broker=Path('/synthetic'),codex=Path('/usr/synthetic'),
            rpc_factory=lambda **kw:self.fail('RPC must not open'))
        with patch('bridge.guest_runner.ExecutorGateway') as gateway:
            result=runner({'operationId':'cancelled-fixture','text':'Synthetic'},lambda:{'stop':True,'stopReason':'cancelled'})
            self.assertEqual(result['status'],'interrupted')
            self.assertEqual(result['stopReason'],'cancelled');gateway.assert_not_called()

    def test_definite_turn_rejection_is_failed_not_uncertain(self):
        calls=[]
        class Rpc:
            def call(self,method,params):
                calls.append(method)
                if method=='turn/start':raise RpcError({'message':'unsupported synthetic model'})
                if method=='thread/start':return {'thread':{'id':'synthetic-thread'}}
                return {}
            def close(self):pass
        gateway=SimpleNamespace(url='ws://synthetic',token='synthetic',close=lambda:None)
        with patch('bridge.guest_runner.ExecutorGateway',return_value=gateway):
            runner=GuestRunner(workspace=Path('/synthetic'),broker=Path('/synthetic'),codex=Path('/usr/synthetic'),rpc_factory=lambda **kw:Rpc())
            result=runner({'operationId':'rejected-fixture','text':'Synthetic'},lambda:{'stop':False})
        self.assertEqual(result['status'],'failed');self.assertEqual(result['stopReason'],'launch_rejected')
        self.assertNotIn('turn/interrupt',calls)

    def test_transport_timeout_still_requires_owner_reconciliation(self):
        class Rpc:
            def call(self,method,params):
                if method=='turn/start':raise TimeoutError('Synthetic lost response')
                if method=='thread/start':return {'thread':{'id':'synthetic-thread'}}
                return {}
            def close(self):pass
        gateway=SimpleNamespace(url='ws://synthetic',token='synthetic',close=lambda:None)
        with patch('bridge.guest_runner.ExecutorGateway',return_value=gateway):
            runner=GuestRunner(workspace=Path('/synthetic'),broker=Path('/synthetic'),codex=Path('/usr/synthetic'),rpc_factory=lambda **kw:Rpc())
            with self.assertRaises(TimeoutError):runner({'operationId':'timeout-fixture','text':'Synthetic'},lambda:{'stop':False})
