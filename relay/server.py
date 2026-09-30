#!/usr/bin/env python3
"""Single-owner TCP relay. The public port carries the PC bridge's TLS unchanged."""

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
import secrets
import ssl


def setting(name: str, default: str) -> str:
    return os.environ.get(name, default)


def proof(secret: bytes, value: str) -> str:
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


async def read_message(reader: asyncio.StreamReader) -> dict:
    line = await reader.readline()
    if not line or len(line) > 4096 or not line.endswith(b"\n"):
        raise ConnectionError("Invalid control message")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ConnectionError("Invalid control message")
    return value


async def write_message(writer: asyncio.StreamWriter, value: dict) -> None:
    writer.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
    await writer.drain()


async def close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


@dataclass
class Pending:
    channel: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class Relay:
    def __init__(self, secret: bytes):
        self.secret = secret
        self.agent: asyncio.StreamWriter | None = None
        self.pending: dict[str, Pending] = {}
        self.lock = asyncio.Lock()

    async def handle_agent(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            challenge = secrets.token_hex(32)
            await write_message(writer, {"challenge": challenge})
            response = await asyncio.wait_for(read_message(reader), 10)
            if not hmac.compare_digest(str(response.get("proof", "")), proof(self.secret, "agent:" + challenge)):
                return
            async with self.lock:
                if self.agent is not None:
                    return
                self.agent = writer
            await write_message(writer, {"ready": True})
            while True:
                message = await asyncio.wait_for(read_message(reader), 65)
                if message.get("type") != "ping":
                    raise ConnectionError("Unexpected control message")
        except (ConnectionError, OSError, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
            pass
        finally:
            async with self.lock:
                if self.agent is writer:
                    self.agent = None
                    for pending in self.pending.values():
                        if not pending.channel.done():
                            pending.channel.set_exception(ConnectionError("PC disconnected"))
            await close(writer)

    async def handle_data(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pending = None
        try:
            message = await asyncio.wait_for(read_message(reader), 10)
            connection_id = message.get("id", "")
            supplied = message.get("proof", "")
            if not isinstance(connection_id, str) or not isinstance(supplied, str):
                return
            if not hmac.compare_digest(supplied, proof(self.secret, "data:" + connection_id)):
                return
            pending = self.pending.get(connection_id)
            if pending is None or pending.channel.done():
                return
            await write_message(writer, {"ready": True})
            pending.channel.set_result((reader, writer))
            await pending.finished.wait()
        except (ConnectionError, OSError, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
            pass
        finally:
            await close(writer)

    async def handle_phone(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection_id = secrets.token_hex(16)
        pending = Pending()
        try:
            async with self.lock:
                if self.agent is None or len(self.pending) >= 128:
                    return
                self.pending[connection_id] = pending
                await write_message(self.agent, {"connect": connection_id})
            data_reader, data_writer = await asyncio.wait_for(pending.channel, 15)
            async def pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                while chunk := await source.read(65536):
                    target.write(chunk)
                    await target.drain()
            tasks = [asyncio.create_task(pipe(reader, data_writer)),
                     asyncio.create_task(pipe(data_reader, writer))]
            done, remaining = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in remaining:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (ConnectionError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.pending.pop(connection_id, None)
            pending.finished.set()
            if pending.channel.done() and not pending.channel.cancelled():
                try:
                    _, data_writer = pending.channel.result()
                    await close(data_writer)
                except ConnectionError:
                    pass
            await close(writer)


async def main() -> None:
    credential_dir = os.environ.get("CREDENTIALS_DIRECTORY", "/etc/remotevibecode")
    secret = open(os.path.join(credential_dir, "relay-secret"), "rb").read().strip()
    if len(secret) < 32:
        raise RuntimeError("Relay secret is missing or too short")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.path.join(credential_dir, "relay-cert"),
                        os.path.join(credential_dir, "relay-key"))
    relay = Relay(secret)
    public_port = int(setting("RV_PUBLIC_PORT", "8765"))
    control_port = int(setting("RV_CONTROL_PORT", "8766"))
    data_port = int(setting("RV_DATA_PORT", "8767"))
    servers = [
        await asyncio.start_server(relay.handle_phone, "0.0.0.0", public_port),
        await asyncio.start_server(relay.handle_agent, "0.0.0.0", control_port, ssl=tls),
        await asyncio.start_server(relay.handle_data, "0.0.0.0", data_port, ssl=tls),
    ]
    print(f"RemoteVibecode relay listening on {public_port}, {control_port}, {data_port}", flush=True)
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
