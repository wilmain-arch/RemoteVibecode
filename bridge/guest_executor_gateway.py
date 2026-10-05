"""Authenticated loopback WebSocket -> isolated exec-server stdio.

The gateway transports executor RPC only. It does not proxy model calls or
account auth. Its token must never be returned to the phone or written to logs.
"""
from __future__ import annotations
import asyncio
import hmac
import json
from pathlib import Path
import queue
import secrets
import threading
from .guest_sandbox import IsolatedExecutor


class ExecutorGateway:
    def __init__(self, workspace: Path, codex: Path, *, resource_limits=False):
        self.workspace=workspace
        self.codex=codex
        self.resource_limits=resource_limits
        self.token=secrets.token_urlsafe(32)
        self.ready=threading.Event()
        self.stop_requested=threading.Event()
        self.owner=threading.Lock()
        self.failure=None
        self.executors=set()
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=5):
            self.close()
            raise RuntimeError('Не удалось запустить канал гостевого исполнителя')
        if self.failure:
            raise RuntimeError('Канал исполнителя недоступен') from self.failure

    def _run(self):
        try:asyncio.run(self._serve())
        except BaseException as exc:self.failure=exc;self.ready.set()

    async def _serve(self):
        from websockets.asyncio.server import serve
        self.loop=asyncio.get_running_loop()
        self.finished=asyncio.Event()
        async def authenticate(connection,request):
            supplied=request.headers.get('Authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+self.token):
                return connection.respond(401,'Executor authentication required\n')
        async with serve(self._session,'127.0.0.1',0,process_request=authenticate,
                         max_size=8*1024*1024,max_queue=16,ping_interval=15,ping_timeout=10) as server:
            self.url='ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
            self.ready.set()
            if self.stop_requested.is_set():self.finished.set()
            await self.finished.wait()

    async def _session(self,connection):
        if not self.owner.acquire(blocking=False):
            await connection.close(1013,'Executor already attached')
            return
        from websockets.exceptions import ConnectionClosed
        executor=None
        try:
            executor=IsolatedExecutor(self.workspace,self.codex,initialize=False,resource_limits=self.resource_limits)
            self.executors.add(executor)
            async def transmit():
                while True:
                    try:message=await asyncio.to_thread(executor.incoming.get,True,1)
                    except queue.Empty:continue
                    if message.get('closed'):
                        await connection.close(1011,'Executor stopped');return
                    await connection.send(json.dumps(message))
            sender=asyncio.create_task(transmit())
            try:
                async for frame in connection:
                    data=json.loads(frame)
                    if not isinstance(data,dict):
                        raise ValueError('Expected executor RPC object')
                    def write():
                        executor.proc.stdin.write(json.dumps(data)+'\n')
                        executor.proc.stdin.flush()
                    await asyncio.wait_for(asyncio.to_thread(write),timeout=5)
            finally:
                sender.cancel()
                await asyncio.gather(sender,return_exceptions=True)
        except (OSError,ValueError,RuntimeError,asyncio.TimeoutError,ConnectionClosed):
            await connection.close(1011,'Executor unavailable')
        finally:
            if executor:
                executor.close()
                self.executors.discard(executor)
            self.owner.release()

    def close(self):
        self.stop_requested.set()
        if hasattr(self,'loop') and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.finished.set)
        self.thread.join(timeout=8)
        if self.thread.is_alive():
            # Terminate the process even if transport shutdown is stalled.
            for executor in tuple(self.executors):executor.close()
            self.thread.join(timeout=3)
        if self.thread.is_alive():raise RuntimeError('Гостевой канал не завершился')
