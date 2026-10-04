"""No paid tasks: isolated streams verify relay deadlines and resource cleanup."""
import asyncio
import unittest
from relay.server import Relay

class Writer:
    def __init__(self): self.closed=False; self.messages=[]
    def get_extra_info(self, name, default=None): return ('synthetic', 1)
    def write(self, value): self.messages.append(value)
    async def drain(self): pass
    def close(self): self.closed=True
    async def wait_closed(self): pass

class RelayResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_channel_frees_resources(self):
        relay=Relay(b'x'*32, idle_timeout=.03, connection_timeout=.2)
        relay.agent=Writer(); phone=Writer(); data=Writer()
        task=asyncio.create_task(relay.handle_phone(asyncio.StreamReader(), phone))
        await asyncio.sleep(.01)
        pending=next(iter(relay.pending.values()))
        pending.channel.set_result((asyncio.StreamReader(), data))
        await asyncio.wait_for(task, .5)
        self.assertEqual(relay.pending, {}); self.assertEqual(relay.peers, {})
        self.assertTrue(phone.closed); self.assertTrue(data.closed)
    async def test_peer_limit_rejects_seventeenth_channel(self):
        relay=Relay(b'x'*32); relay.agent=Writer(); relay.peers['synthetic']=16
        phone=Writer()
        await relay.handle_phone(asyncio.StreamReader(), phone)
        self.assertTrue(phone.closed); self.assertEqual(relay.pending, {})
        self.assertEqual(len(relay.agent.messages), 0)
    async def test_cancel_closes_pipes_and_releases_slot(self):
        relay=Relay(b'x'*32); relay.agent=Writer(); phone=Writer(); data=Writer()
        task=asyncio.create_task(relay.handle_phone(asyncio.StreamReader(), phone))
        await asyncio.sleep(.01)
        next(iter(relay.pending.values())).channel.set_result((asyncio.StreamReader(), data))
        await asyncio.sleep(.01); task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(relay.pending, {}); self.assertEqual(relay.peers, {})
        self.assertTrue(phone.closed); self.assertTrue(data.closed)
