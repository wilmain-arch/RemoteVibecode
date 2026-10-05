#!/usr/bin/env python3
"""Small authenticated HTTP bridge from an Android client to Codex app-server."""

from __future__ import annotations

import argparse
import codecs
import base64
from io import BytesIO
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import queue
import re
import secrets
import ssl
import socket
import subprocess
import threading
import time
import uuid
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote
# Script and packaged-agent entry points use the same package imports.
if not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    __package__='bridge'
    sys.modules.setdefault('bridge.server',sys.modules[__name__])
try:
    from .adb_devices import AdbDevices
    from .interactions import InteractionInbox
    from .controls import Controls
    from .workspace_listing import listing
    from .guest_access import GuestAccess, AccessError
    from .guest_jobs import GuestJobs
except ImportError:
    from adb_devices import AdbDevices
    from interactions import InteractionInbox
    from controls import Controls
    from workspace_listing import listing
    from guest_access import GuestAccess, AccessError
    from guest_jobs import GuestJobs

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None


MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
RPC_IDLE_SECONDS = 15
THREAD_SOURCES = ["cli", "vscode", "appServer"]
VALID_THREAD_ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')


class CodexRpc:
    def __init__(self, on_event=None, *, executable_override=None, process_env=None, config_args=(), process_cwd=None) -> None:
        try:
            from .codex_path import resolve_codex_executable
        except ImportError:
            from codex_path import resolve_codex_executable
        executable = resolve_codex_executable(executable_override or os.environ.get("CODEX_EXECUTABLE"))
        if not executable:
            raise RuntimeError("Не найден исполняемый файл Codex Desktop или CLI")
        app_server_args = ["app-server", "--listen", "stdio://"] if sys.platform == "win32" else ["app-server", "--stdio"]
        self.executable = executable
        self.healthy = False
        self.proc = subprocess.Popen(
            [executable, *app_server_args, *config_args],
            env=process_env,
            cwd=process_cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._write_lock = threading.Lock()
        self._waiters: dict[int, queue.Queue] = {}
        self._waiters_lock = threading.Lock()
        self._id = 0
        self.events: queue.Queue = queue.Queue(maxsize=1000)
        self.on_event = on_event
        self.interactions = InteractionInbox(self._send)
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {
                "clientInfo": {"name": "codex-phone-companion", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.notify("initialized", {})
            self.healthy = True
        except BaseException:
            self.close()
            raise

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = data.get("id")
            if data.get("method") and "id" in data:
                if self.interactions.receive(data):
                    if self.on_event:
                        self.on_event(data)
                else:
                    self._send({"id": request_id, "error": {"code": -32601, "message": "Unsupported client request"}})
            elif isinstance(request_id, int):
                with self._waiters_lock:
                    waiter = self._waiters.get(request_id)
                if waiter is not None:
                    try:
                        waiter.put_nowait(data)
                    except queue.Full:
                        pass
            elif data.get("method"):
                if data["method"] == "serverRequest/resolved":
                    self.interactions.resolve((data.get("params") or {}).get("requestId"))
                if self.on_event:
                    self.on_event(data)
                try:
                    self.events.put_nowait(data)
                except queue.Full:
                    pass
        self.healthy = False
        # EOF completes outstanding requests immediately instead of an opaque timeout.
        with self._waiters_lock:
            for waiter in self._waiters.values():
                try:
                    waiter.put_nowait({"error": {"message": "Codex app-server закрыл соединение", "code": -32001}})
                except queue.Full:
                    pass

    def _send(self, data: dict) -> None:
        if self.proc.poll() is not None:
            raise RuntimeError("Codex app-server остановлен")
        assert self.proc.stdin is not None
        with self._write_lock:
            self.proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        with self._waiters_lock:
            self._id += 1
            request_id = self._id
            waiter: queue.Queue = queue.Queue(maxsize=1)
            self._waiters[request_id] = waiter
        try:
            self._send({"method": method, "id": request_id, "params": params})
            try:
                response = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                self.healthy = False
                raise TimeoutError("Codex не ответил вовремя: " + method) from exc
            if "error" in response:
                error = response["error"]
                raise RpcError(error.get("message", "Codex request failed"), error.get("code"))
            self.healthy = True
            return response.get("result", {})
        finally:
            with self._waiters_lock:
                self._waiters.pop(request_id, None)

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        if threading.current_thread() is not self.reader:
            self.reader.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None:
                stream.close()


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class Bridge:
    def __init__(self, thread_id: str, upload_dir: Path, outbox_dir: Path, pin: str, state_file: Path):
        self.thread_id = thread_id
        self.upload_dir = upload_dir.resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.chmod(0o700)
        self.outbox_dir = outbox_dir.resolve()
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.chmod(0o700)
        self.state_file = state_file.resolve()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_lock = threading.RLock()
        self.guests = GuestAccess(self.state_file.with_name("guest-access.sqlite3"))
        self.guest_jobs = GuestJobs(self.guests)
        self.guest_jobs.recover()
        self.guest_runtime = None
        self.guest_display_lock = threading.Lock()
        self.guest_display_cache = None
        self.guest_display_checked = 0.0
        from bridge.guest_resources import GuestResources
        self.guest_resources = GuestResources(self)
        self.pin = pin
        self.pin_uses = 0
        self.pin_lock = threading.Lock()
        saved = self._load_state()
        self.token = saved.get("token") or secrets.token_urlsafe(32)
        self.paired = bool(saved.get("paired", False))
        self.guests.bind_owner(self.token)
        self.guest_execution_enabled = bool(saved.get("guest_execution_enabled", False))
        self.selected_thread_id = saved.get("selected_thread_id") or thread_id
        self.draft_threads: dict[str, str | None] = saved.get("draft_threads", {})
        self.project_overrides: dict[str, str] = saved.get("project_overrides", {})
        self.task_usage: dict[str, dict] = saved.get("task_usage", {})
        self.usage_events: queue.Queue = queue.Queue(maxsize=2000)
        if thread_id.startswith("draft-") and thread_id not in self.draft_threads:
            self.draft_threads[thread_id] = str(Path.home())
        self.rpc: CodexRpc | None = None
        self.rpc_lock = threading.RLock()
        self.rpc_last_used = 0.0
        self.last_rpc_health = False
        self.active_turns: dict[str, str] = {}
        self.turn_lock = threading.Lock()
        self.event_condition = threading.Condition()
        self.event_instance = secrets.token_hex(8)
        self.event_count = 0
        self.send_lock = threading.Lock()
        self.submission_lock = threading.Lock()
        self.update_until = 0.0
        self.upload_lock = threading.Lock()
        self.pending: dict[str, dict] = {
            key: item for key, item in saved.get("pending", {}).items()
            if isinstance(item, dict) and Path(item.get("path", "")).is_file()
            and Path(item["path"]).resolve().parent == self.upload_dir
        }
        self.sent_messages: dict[str, dict] = saved.get("sent_messages", {})
        self.send_operations = saved.get("send_operations", {})
        self.queued_sends: dict[str, dict] = saved.get("queued_sends", {})
        self.thread_modes = saved.get("thread_modes", {})
        self.published_plans = saved.get("published_plans", {})
        self.control_operations = saved.get("control_operations", {})
        self.plan_boundaries = saved.get("plan_boundaries", {})
        self.controls = Controls(self)
        self.cancelled_sends: dict[str, float] = saved.get("cancelled_sends", {})
        self.dismissed_sends = saved.get("dismissed_sends", {})
        self.last_queue_error = saved.get("last_queue_error", "")
        self.queue_lock = threading.Lock()
        self.chat_files: dict[tuple[str, str], Path] = {}
        self.chat_images: dict[str, Path] = {}
        self.chat_images_lock = threading.Lock()
        self.agent_parents = {}
        self.agent_parents_lock = threading.RLock()
        self.agent_labels = {}
        self.agent_labels_lock = threading.Lock()
        self._save_state()
        self.stop_worker = threading.Event()
        self.worker = threading.Thread(target=self._deliver_queued, daemon=True)
        self.worker.start()
        self.rpc_idle_worker = threading.Thread(target=self._close_idle_rpc, daemon=True)
        self.rpc_idle_worker.start()
        threading.Thread(target=self._track_task_usage, daemon=True).start()
        if sys.platform == "linux":
            from bridge.guest_runtime import GuestRuntime
            self.guest_runtime = GuestRuntime(self)
            if self.guest_execution_enabled:
                try: self.guest_runtime.configure(True)
                except Exception:
                    self.guest_runtime.reason = "Гостевой исполнитель недоступен: проверьте зависимости и Codex"


    @contextmanager
    def rpc_session(self):
        with self.rpc_lock:
            if self.rpc is None or self.rpc.proc.poll() is not None:
                with self.turn_lock:
                    self.active_turns.clear()
                self.last_rpc_health = False
                self.rpc = CodexRpc(self._signal_event)
            rpc = self.rpc
            try:
                yield rpc
            finally:
                self.last_rpc_health = bool(getattr(rpc, "healthy", False))
                self.rpc_last_used = time.monotonic()

    def _signal_event(self, event: dict | None = None) -> None:
        if event:
            method = event.get("method", "")
            params = event.get("params") or {}
            thread_id = params.get("threadId")
            self._remember_agent_relations(thread_id, [params.get("item") or {}])
            if method == "turn/plan/updated" and isinstance(thread_id, str) and isinstance(params.get("turnId"), str):
                plan = params.get("plan") or []
                if isinstance(plan, list):
                    steps = [{"step": str(p.get("step", ""))[:1000], "status": p.get("status", "pending")}
                             for p in plan[:100] if isinstance(p, dict)]
                    with self.state_lock:
                        self.published_plans[params["turnId"]] = {"threadId": thread_id, "steps": steps,
                            "explanation": str(params.get("explanation") or "")[:2000]}
                        while len(self.published_plans) > 200:
                            self.published_plans.pop(next(iter(self.published_plans)))
                        self._save_state()

            turn = params.get("turn") or {}
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if method in ("turn/started", "turn/completed", "thread/tokenUsage/updated", "model/rerouted"):
                try:
                    self.usage_events.put_nowait((method, params))
                except queue.Full:
                    pass
            with self.turn_lock:
                if method == "turn/started" and thread_id and turn_id:
                    self.active_turns[thread_id] = turn_id
                elif method in ("turn/completed", "turn/failed", "turn/aborted", "turn/cancelled"):
                    if thread_id:
                        self.active_turns.pop(thread_id, None)
                    elif turn_id:
                        self.active_turns = {key: value for key, value in self.active_turns.items()
                                             if value != turn_id}
        with self.event_condition:
            self.event_count += 1
            self.event_condition.notify_all()

    def _track_task_usage(self) -> None:
        # Never make RPC calls from the notification reader: it must remain free
        # to deliver the responses awaited by callers.
        while not self.stop_worker.is_set():
            try:
                method, params = self.usage_events.get(timeout=1)
            except queue.Empty:
                continue
            turn = params.get("turn") or {}
            turn_id = params.get("turnId") or turn.get("id")
            if not turn_id:
                continue
            try:
                if method == "turn/started":
                    with self.state_lock:
                        needs_baseline = "before" not in self.task_usage.get(turn_id, {})
                    snapshot = self.usage_limits() if needs_baseline else None
                    with self.state_lock:
                        record = self.task_usage.setdefault(turn_id, {})
                        record.setdefault("threadId", params.get("threadId"))
                        if snapshot:
                            record.setdefault("before", snapshot)
                            record["baselineObserved"] = True
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage") or {}
                    total, last = usage.get("total") or {}, usage.get("last") or {}
                    with self.state_lock:
                        record = self.task_usage.setdefault(turn_id, {"threadId": params.get("threadId")})
                        if "tokenBaseline" not in record:
                            record["tokenBaseline"] = {key: max(0, value - last.get(key, 0))
                                for key, value in total.items() if isinstance(value, int)}
                        record["tokens"] = {key: max(0, value - record["tokenBaseline"].get(key, value))
                            for key, value in total.items() if isinstance(value, int)}
                elif method == "turn/completed":
                    snapshot = self.usage_limits()
                    with self.state_lock:
                        record = self.task_usage.setdefault(turn_id, {"threadId": params.get("threadId")})
                        record["after"] = snapshot
                elif method == "model/rerouted":
                    with self.state_lock:
                        record = self.task_usage.setdefault(turn_id, {"threadId": params.get("threadId")})
                        notices = record.setdefault("notices", [])
                        text = f"Модель изменена сервером: {params.get('fromModel', '?')} → {params.get('toModel', '?')}"
                        if text not in notices:
                            notices.append(text)
                with self.state_lock:
                    while len(self.task_usage) > 2000:
                        self.task_usage.pop(next(iter(self.task_usage)))
                    self._save_state()
                self._signal_event()
            except (RpcError, RuntimeError, OSError, queue.Empty):
                # Usage failures must not stop message delivery.
                continue
            finally:
                with self.state_lock:
                    self.task_usage.get(turn_id, {}).pop("observationQueued", None)

    def wait_event(self, cursor: str, timeout: float = 20.0) -> dict:
        with self.event_condition:
            current = f"{self.event_instance}:{self.event_count}"
            if cursor == current:
                self.event_condition.wait_for(
                    lambda: f"{self.event_instance}:{self.event_count}" != cursor,
                    timeout=max(1.0, min(timeout, 25.0)),
                )
            return {"cursor": f"{self.event_instance}:{self.event_count}"}

    def _close_idle_rpc(self) -> None:
        while not self.stop_worker.wait(3):
            with self.rpc_lock:
                if self.rpc is not None and time.monotonic() - self.rpc_last_used >= RPC_IDLE_SECONDS:
                    with self.turn_lock:
                        active = dict(self.active_turns)
                    if active:
                        for thread_id, turn_id in active.items():
                            try:
                                result = self.rpc.call("thread/read", {"threadId": thread_id,
                                                                        "includeTurns": True}, timeout=10)
                                turns = result.get("thread", {}).get("turns", [])
                                current = next((turn for turn in turns if turn.get("id") == turn_id), None)
                                state = current.get("status") if current else None
                                kind = state.get("type") if isinstance(state, dict) else state
                                if kind in ("completed", "failed", "interrupted", "cancelled"):
                                    with self.turn_lock:
                                        if self.active_turns.get(thread_id) == turn_id:
                                            self.active_turns.pop(thread_id, None)
                            except Exception:
                                # Losing the status query must never cancel a running turn.
                                pass
                        with self.turn_lock:
                            if self.active_turns:
                                self.rpc_last_used = time.monotonic()
                                continue
                    if self.rpc.interactions.has_pending():
                        self.rpc_last_used = time.monotonic()
                        continue
                    self.last_rpc_health = bool(getattr(self.rpc, "healthy", False))
                    self.rpc.close()
                    self.rpc = None

    def _thread_id(self, thread_id: str | None = None) -> str:
        value = thread_id or self.selected_thread_id
        if not isinstance(value, str) or not VALID_THREAD_ID.fullmatch(value):
            raise ValueError("Некорректный ID чата")
        return value

    def update_status(self, arm: bool = False, release: bool = False) -> dict:
        """Local updater waits for running Desktop/phone turns and the persisted queue."""
        with self.send_lock:
            if release:
                self.update_until = 0.0
                return {"armed": False}
            with self.turn_lock:
                active = len(self.active_turns)
            with self.queue_lock:
                queued = len(self.queued_sends)
            if getattr(self, "guest_runtime", None) and self.guest_runtime.busy.is_set(): active += 1
            # Detect tasks started in Desktop, not just through this bridge.
            active = max(active, sum(t.get("status") in ("active", "inProgress")
                for t in self.list_threads(limit=10000)))
            if arm and not active and not queued:
                self.update_until = time.monotonic() + 120
            return {"active": active, "queued": queued,
                    "ready": not active and not queued,
                    "armed": time.monotonic() < self.update_until}

    def list_threads(self, limit: int = 100) -> list[dict]:
        threads: list[dict] = []
        seen_ids = set(self.draft_threads)
        cursor = None
        with self.rpc_session() as rpc:
            while len(threads) < limit:
                params = {"limit": min(100, limit - len(threads)), "sortKey": "updated_at",
                          "sourceKinds": THREAD_SOURCES}
                if cursor:
                    params["cursor"] = cursor
                result = rpc.call("thread/list", params)
                for thread in result.get("data", []):
                    thread_id = thread.get("id")
                    if not isinstance(thread_id, str) or thread_id in seen_ids:
                        continue
                    seen_ids.add(thread_id)
                    status = thread.get("status") or {}
                    threads.append({
                        "id": thread_id,
                        "title": thread.get("name") or thread.get("preview") or "Новый чат",
                        "preview": thread.get("preview") or "",
                        "cwd": thread.get("cwd"),
                        "projectId": thread.get("projectId"),
                        "model": thread.get("model"),
                        "reasoningEffort": thread.get("reasoningEffort"),
                        "status": status.get("type", "unknown") if isinstance(status, dict) else status,
                        "updatedAt": thread.get("updatedAt"),
                    })
                cursor = result.get("nextCursor")
                if not cursor or not result.get("data"):
                    break
        drafts = [{"id": thread_id, "title": "Новый чат", "preview": "", "cwd": cwd,
                   "projectId": None, "model": None, "reasoningEffort": None,
                   "status": "draft", "updatedAt": None}
                  for thread_id, cwd in self.draft_threads.items()]
        return (drafts + threads)[:limit]

    def subagents(self, thread_id: str | None) -> dict:
        root_id = self._thread_id(thread_id)
        if root_id in self.draft_threads:
            return {"agents": []}
        with self.rpc_session() as rpc:
            root = rpc.call("thread/read", {"threadId": root_id, "includeTurns": True})["thread"]
            hints = {}
            for turn in root.get("turns", []):
                for item in turn.get("items", []):
                    if item.get("type") == "collabAgentToolCall":
                        for agent_id in item.get("receiverThreadIds", []):
                            hint = hints.setdefault(agent_id, {})
                            if item.get("tool") == "spawnAgent":
                                hint["task"] = item.get("prompt") or ""
                            state = (item.get("agentsStates") or {}).get(agent_id) or {}
                            hint.update({key: value for key, value in state.items() if value is not None})
            agents, cursor = [], None
            while True:
                params = {"ancestorThreadId": root_id, "limit": 100,
                          "sourceKinds": ["subAgent", "subAgentReview", "subAgentCompact",
                                          "subAgentThreadSpawn", "subAgentOther"],
                          "sortKey": "created_at"}
                if cursor:
                    params["cursor"] = cursor
                result = rpc.call("thread/list", params)
                for thread in result.get("data", []):
                    agent_id = thread["id"]
                    hint = hints.get(agent_id, {})
                    status = (thread.get("status") or {}).get("type", "unknown")
                    agents.append({"id": agent_id, "parentId": thread.get("parentThreadId"),
                                   "name": thread.get("agentNickname") or thread.get("name") or "Субагент",
                                   "model": thread.get("model") or "",
                                   "role": thread.get("agentRole") or "",
                                   "task": hint.get("task") or thread.get("preview") or "",
                                   "status": "running" if status == "active" else (
                                       hint["status"] if hint.get("status") in
                                       ("completed", "interrupted", "errored", "shutdown") else status),
                                   "message": hint.get("message") or "",
                                   "canSend": thread.get("canAcceptDirectInput") is not False})
                cursor = result.get("nextCursor")
                if not cursor or not result.get("data") or len(agents) >= 500:
                    break
            for agent in agents:
                if not agent["model"]:
                    try:
                        detail = rpc.call("thread/read", {"threadId": agent["id"], "includeTurns": False})["thread"]
                        agent["model"] = detail.get("model") or ""
                    except RpcError:
                        pass
            known_ids = {agent["id"] for agent in agents}
            for agent_id, hint in hints.items():
                if agent_id in known_ids:
                    continue
                try:
                    thread = rpc.call("thread/read", {"threadId": agent_id, "includeTurns": False})["thread"]
                except RpcError:
                    continue
                if thread.get("parentThreadId") != root_id:
                    continue
                status = (thread.get("status") or {}).get("type", "unknown")
                agents.append({"id": agent_id, "parentId": root_id,
                               "name": thread.get("agentNickname") or "Субагент",
                               "model": thread.get("model") or "",
                               "role": thread.get("agentRole") or "",
                               "task": hint.get("task") or thread.get("preview") or "",
                               "status": "running" if status == "active" else (
                                       hint["status"] if hint.get("status") in
                                       ("completed", "interrupted", "errored", "shutdown") else status),
                               "message": hint.get("message") or "",
                               "canSend": thread.get("canAcceptDirectInput") is not False})
        return {"agents": agents}

    def subagent_check(self, thread_id: str | None, agent_id: str) -> dict:
        agent_id = self._thread_id(agent_id)
        agent = next((agent for agent in self.subagents(thread_id)["agents"]
                      if agent["id"] == agent_id), None)
        if agent is None:
            raise ValueError("Субагент не принадлежит текущему чату")
        return agent

    def subagent_action(self, data: dict) -> dict:
        thread_id, agent_id = data.get("threadId"), data.get("agentId")
        agent = self.subagent_check(thread_id, agent_id)
        action = data.get("action")
        if action not in ("send", "interrupt"):
            raise ValueError("Неизвестное действие")
        # Send paths always acquire send_lock before rpc_lock; interrupt needs only RPC.
        with (self.send_lock if action == "send" else nullcontext()), self.rpc_session() as rpc:
            thread = rpc.call("thread/read", {"threadId": agent_id, "includeTurns": True})["thread"]
            active = next((turn for turn in reversed(thread.get("turns", []))
                           if turn.get("status") == "inProgress"), None)
            if action == "interrupt":
                if active:
                    rpc.call("turn/interrupt", {"threadId": agent_id, "turnId": active["id"]})
                return {"accepted": True, "message": "Остановка запрошена" if active else "Агент уже не выполняет задачу"}
            text, message_id = data.get("text"), data.get("messageId")
            if not isinstance(text, str) or not text.strip() or len(text) > 32000:
                raise ValueError("Введите сообщение до 32 000 символов")
            if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", message_id):
                raise ValueError("Некорректный ID сообщения")
            if not agent["canSend"] or thread.get("canAcceptDirectInput") is False:
                raise ValueError("Desktop не разрешает прямой ввод этому агенту")
            if active:
                if time.monotonic() < self.update_until:
                    raise ValueError("Агент ПК устанавливает обновление")
                if message_id in self.sent_messages:
                    return self.sent_messages[message_id]
                result = rpc.call("turn/steer", {"threadId": agent_id,
                    "expectedTurnId": active["id"], "clientUserMessageId": message_id,
                    "input": [{"type": "text", "text": text.strip()}]})
                response = {"accepted": True, "message": "Уточнение отправлено", "turnId": result.get("turnId")}
                with self.state_lock:
                    self.sent_messages[message_id] = response
                    self._save_state()
                return response
        self.send(text, [], message_id, agent_id)
        return {"accepted": True, "message": "Задача отправлена"}

    def _remember_agent_relations(self, thread_id: str | None, items: list[dict]) -> None:
        if not thread_id:
            return
        with self.agent_parents_lock:
            for item in items:
                children = ([item.get("agentThreadId")] if item.get("type") == "subAgentActivity"
                            else item.get("receiverThreadIds") or [] if item.get("type") == "collabAgentToolCall" and item.get("tool") == "spawnAgent" else [])
                for child in children:
                    if child and child != thread_id:
                        self.agent_parents[child] = thread_id
                while len(self.agent_parents) > 2000:
                    self.agent_parents.pop(next(iter(self.agent_parents)))

    def requests(self, thread_id: str | None) -> dict:
        thread_id = self._thread_id(thread_id)
        rpc = self.rpc
        with self.agent_parents_lock:
            descendants = set()
            for child in self.agent_parents:
                parent, seen = child, set()
                while parent in self.agent_parents and parent not in seen:
                    seen.add(parent)
                    parent = self.agent_parents[parent]
                    if parent == thread_id:
                        descendants.add(child)
                        break
        return {"requests": rpc.interactions.list(thread_id, descendants) if rpc and rpc.proc.poll() is None else []}

    def respond_request(self, data: dict) -> dict:
        thread_id = self._thread_id(data.get("threadId"))
        rpc = self.rpc
        if rpc is None or rpc.proc.poll() is not None:
            raise ValueError("Сессия Codex закрыта; обновите чат")
        result = rpc.interactions.respond(thread_id, data.get("id"), data.get("response"))
        self._signal_event()
        return result

    def interrupt_turn(self, data: dict) -> dict:
        thread_id = self._thread_id(data.get("threadId"))
        expected = data.get("turnId")
        if not isinstance(expected, str) or not expected:
            raise ValueError("Обновите чат перед остановкой")
        with self.rpc_session() as rpc:
            thread = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
            active = next((turn for turn in reversed(thread.get("turns") or [])
                           if turn.get("status") == "inProgress"), None)
            if not active:
                return {"accepted": True, "message": "Задача уже завершена"}
            if active.get("id") != expected:
                raise ValueError("Уже выполняется другая задача; обновите чат")
            rpc.call("turn/interrupt", {"threadId": thread_id, "turnId": expected})
        self._signal_event()
        return {"accepted": True, "message": "Остановка запрошена"}

    def task_result(self, thread_id: str | None, turn_id: str) -> dict:
        thread_id = self._thread_id(thread_id)
        if not isinstance(turn_id, str) or not VALID_THREAD_ID.fullmatch(turn_id):
            raise ValueError("Некорректный ID задачи")
        with self.rpc_session() as rpc:
            thread = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        turn = next((t for t in thread.get("turns") or [] if t.get("id") == turn_id), None)
        if turn is None:
            return {"found": False}
        outcome, quota = self.task_outcome_summary(turn)
        active = next((t for t in reversed(thread.get("turns") or []) if t.get("status") == "inProgress"), None)
        with self.queue_lock:
            queued_count = sum(1 for item in self.queued_sends.values() if (item.get("threadId") or self.thread_id) == thread_id)
        return {"found": True, "turnId": turn_id, "status": turn.get("status"),
                "activeTurnId": active.get("id", "") if active else "", "queuedCount": queued_count,
                "outcomeSummary": outcome, "quotaSummary": quota}

    def changes(self, thread_id: str | None, turn_id: str | None = None) -> dict:
        thread_id = self._thread_id(thread_id)
        if thread_id in self.draft_threads:
            return {"changes": [], "truncated": False}
        with self.rpc_session() as rpc:
            thread = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        changes, budget, truncated = [], 512 * 1024, False
        for turn in reversed(thread.get("turns") or []):
            if turn_id and turn.get("id") != turn_id:
                continue
            for item in turn.get("items") or []:
                if item.get("type") != "fileChange":
                    continue
                for change in item.get("changes") or []:
                    if len(changes) >= 100 or budget <= 0:
                        truncated = True
                        break
                    diff = str(change.get("diff") or "")
                    full = len(diff)
                    diff = diff[:min(budget, 128 * 1024)]
                    budget -= len(diff)
                    truncated |= len(diff) < full
                    changes.append({"id": f"{turn['id']}:{item.get('id')}:{len(changes)}",
                                    "turnId": turn['id'], "path": str(change.get("path") or ""),
                                    "kind": change.get("kind"), "status": item.get("status"),
                                    "diff": diff, "truncated": len(diff) < full})
        return {"changes": changes, "truncated": truncated}

    def usage_limits(self) -> dict:
        with self.rpc_session() as rpc:
            result = rpc.call("account/rateLimits/read", {}, timeout=8)
        rate_limits = result.get("rateLimits") or {}
        def window(value):
            if not isinstance(value, dict) or not isinstance(value.get("usedPercent"), (int, float)):
                return None
            used = max(0, min(100, round(value["usedPercent"])))
            return {"remainingPercent": 100 - used,
                    "resetsAt": value.get("resetsAt") if isinstance(value.get("resetsAt"), int) else None}
        return {"fiveHours": window(rate_limits.get("primary")),
                "week": window(rate_limits.get("secondary")),
                "resetCredits": (result.get("rateLimitResetCredits") or {}).get("availableCount"),
                "updatedAt": int(time.time())}

    def guest_display(self,guest):
        result=self.guests.view(guest)
        if not any(q['rule']['mode']!='unlimited' for q in result['quotas'].values()):return result
        with self.guests.lock:
            active=self.guests.db.execute("SELECT 1 FROM execution WHERE guest=? AND state IN ('active','uncertain') LIMIT 1",(guest,)).fetchone()
        if active:return result
        try:
            with self.guest_display_lock:
                if self.guest_display_cache is None or time.monotonic()-self.guest_display_checked>10:
                    self.guest_display_cache=self.guest_usage_limits()
                    self.guest_display_checked=time.monotonic()
                self.guests.synchronize_idle(guest,self.guest_display_cache)
            return self.guests.view(guest)
        except (AccessError,RpcError,RuntimeError,queue.Empty,OSError):
            result['quotaSyncPending']=True
            return result

    def guest_usage_limits(self) -> dict:
        # Never silently interpret a provider-specific primary bucket as 5h.
        with self.rpc_session() as rpc:
            result = rpc.call("account/rateLimits/read", {}, timeout=8)
        limits = result.get("rateLimits") or {}
        snapshot = {}
        for value in (limits.get("primary"), limits.get("secondary")):
            if not isinstance(value, dict):
                continue
            duration = value.get("windowDurationMins")
            key = {300: "fiveHours", 10080: "week"}.get(duration)
            used = value.get("usedPercent")
            if key and isinstance(used, (int, float)) and not isinstance(used, bool):
                snapshot[key] = {"remainingPercent": 100 - used, "resetsAt": value.get("resetsAt")}
        return snapshot

    def reset_limits(self, idempotency_key: str) -> dict:
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", idempotency_key):
            raise ValueError("Некорректный идентификатор сброса")
        with self.rpc_session() as rpc:
            result = rpc.call("account/rateLimitResetCredit/consume", {"idempotencyKey": idempotency_key})
        return {"outcome": result.get("outcome"), "limits": self.usage_limits()}

    def task_usage_text(self, turn_id: str) -> str:
        with self.state_lock:
            record = dict(self.task_usage.get(turn_id, {}))
        tokens = record.get("tokens") or {}
        parts = []
        if "totalTokens" in tokens:
            parts.append(f"Наблюдаемые токены: {tokens['totalTokens']:,} · вход {tokens.get('inputTokens', 0):,} · выход {tokens.get('outputTokens', 0):,}")
        if record and "totalTokens" not in tokens:
            parts.append("Данные о токенах не получены от Codex.")
        if record.get("baselineObserved"):
            parts.append("Начальный замер получен после начала работы.")
        before, after = record.get("before") or {}, record.get("after") or {}
        deltas = []
        for key, label in (("fiveHours", "5ч"), ("week", "неделя")):
            start, end = before.get(key), after.get(key)
            if start and end and start.get("resetsAt") == end.get("resetsAt"):
                change = start["remainingPercent"] - end["remainingPercent"]
                deltas.append(f"{label}: +{change} п.п." if change >= 0 else f"{label}: лимит восстановился")
            else:
                deltas.append(f"{label}: нет сопоставимого замера")
        if record:
            parts.append("Изменение лимитов за время задачи · " + " · ".join(deltas))
            parts.append("Лимиты общие для аккаунта; замер может включать другие чаты и округлён сервером.")
        else:
            parts.append("Расход задачи не измерялся.")
        return "\n".join(parts)

    def task_outcome_summary(self, turn: dict) -> tuple[str, str]:
        duration = turn.get("durationMs")
        if not isinstance(duration, (int, float)):
            start, end = turn.get("startedAt"), turn.get("completedAt")
            duration = (end - start) * 1000 if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
        labels = {"completed": "Выполнено", "interrupted": "Прервано", "failed": "Ошибка"}
        headline = labels.get(turn.get("status"), "Завершено")
        if duration is not None and duration >= 0:
            seconds = int(duration / 1000)
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            elapsed = (f"{hours} ч " if hours else "") + (f"{minutes} мин " if minutes or hours else "") + f"{seconds} с"
            headline += " за " + elapsed
        with self.state_lock:
            record = self.task_usage.get(str(turn.get("id") or ""), {})
            before, after = record.get("before") or {}, record.get("after") or {}
            percentages = []
            measured = 0
            for key, label in (("fiveHours", "за 5ч"), ("week", "недельного")):
                start, end = before.get(key), after.get(key)
                if start and end and start.get("resetsAt") == end.get("resetsAt") and start["remainingPercent"] >= end["remainingPercent"]:
                    measured += 1
                    percentages.append(f"{start['remainingPercent'] - end['remainingPercent']}% {label}")
                else:
                    percentages.append(f"{label}: нет замера")
        return headline, " и ".join(percentages) if measured else "Расход не измерен"

    def projects(self) -> dict:
        threads = self.list_threads(limit=500)
        native_projects = []
        cursor = None
        with self.rpc_session() as rpc:
            while True:
                params = {"limit": 100, "sortKey": "position"}
                if cursor:
                    params["cursor"] = cursor
                page = rpc.call("project/list", params)
                native_projects.extend(page.get("data", []))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        state_path = Path.home() / ".codex" / ".codex-global-state.json"
        try:
            desktop = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            desktop = {}
        known = desktop.get("local-projects") or {}
        if not isinstance(known, dict):
            known = {}
        assignments = desktop.get("thread-project-assignments") or {}
        if not isinstance(assignments, dict):
            assignments = {}
        projectless_ids = set(desktop.get("projectless-thread-ids") or [])
        groups: dict[str, dict] = {}
        roots: dict[str, list[str]] = {}
        for project in native_projects:
            project_id = project.get("id")
            if not isinstance(project_id, str) or not project_id:
                continue
            project_roots = [root.get("path") for root in project.get("roots", [])
                             if isinstance(root, dict) and isinstance(root.get("path"), str)]
            roots[project_id] = project_roots
            groups[project_id] = {
                "id": project_id,
                "name": project.get("name") or (Path(project_roots[0]).name if project_roots else project_id),
                "cwd": project_roots[0] if project_roots else None,
                "threads": [],
            }
        standalone = {"id": "other", "name": "Чаты", "cwd": None, "threads": []}
        legacy_ids = {}
        for old_id, old_project in known.items():
            if not isinstance(old_project, dict):
                continue
            old_roots = set(old_project.get("rootPaths") or [])
            legacy_ids[old_id] = next((id for id, paths in roots.items()
                if old_project.get("name") == groups[id]["name"] and old_roots.intersection(paths)), None)
        for thread in threads:
            thread_id = thread["id"]
            assignment = assignments.get(thread_id) or {}
            native_id = thread.get("projectId")
            if native_id in groups:
                project_id = native_id
            elif thread_id in self.project_overrides:
                project_id = self.project_overrides[thread_id]
            else:
                project_id = legacy_ids.get(assignment.get("projectId"))
            if thread_id in projectless_ids and not native_id and thread_id not in self.project_overrides:
                project_id = None
            if project_id not in groups and thread_id not in projectless_ids \
                    and thread_id not in self.project_overrides:
                cwd = thread.get("cwd")
                project_id = next((id for id, paths in sorted(roots.items(), key=lambda pair: len(pair[1]))
                                   if cwd in paths), None)
            (groups[project_id] if project_id in groups else standalone)["threads"].append(thread)
        return {"projects": [*groups.values(), standalone],
                "selectedThreadId": self.selected_thread_id}

    def assign_thread_project(self, thread_id: str, project_id: str) -> dict:
        if not isinstance(thread_id, str) or not VALID_THREAD_ID.fullmatch(thread_id):
            raise ValueError("Некорректный ID чата")
        if not isinstance(project_id, str):
            raise ValueError("Некорректный ID проекта")
        catalog = self.projects()["projects"]
        if project_id and not any(item["id"] == project_id for item in catalog if item["id"] != "other"):
            raise ValueError("Проект не найден")
        if thread_id not in self.draft_threads and not any(
                thread["id"] == thread_id for group in catalog for thread in group["threads"]):
            raise ValueError("Чат не найден")
        if thread_id in self.draft_threads:
            if project_id:
                target = next(item for item in catalog if item["id"] == project_id)
                self.draft_threads[thread_id] = target["cwd"]
            else:
                self.draft_threads[thread_id] = str(Path.home())
        else:
            with self.rpc_session() as rpc:
                rpc.call("thread/metadata/update", {"threadId": thread_id, "projectId": project_id})
        with self.state_lock:
            self.project_overrides[thread_id] = project_id
            self._save_state()
        return {"threadId": thread_id, "projectId": project_id}

    def models(self) -> dict:
        with self.rpc_session() as rpc:
            result = rpc.call("model/list", {"limit": 100, "includeHidden": False})
        return {"models": [
            {"id": item.get("id"), "name": item.get("displayName") or item.get("id"),
             "defaultEffort": item.get("defaultReasoningEffort"),
             "efforts": [option.get("reasoningEffort") for option in item.get("supportedReasoningEfforts", [])
                         if option.get("reasoningEffort")]}
            for item in result.get("data", []) if item.get("id") and not item.get("hidden")
        ]}

    def select_thread(self, thread_id: str) -> dict:
        thread_id = self._thread_id(thread_id)
        if thread_id not in self.draft_threads:
            with self.rpc_session() as rpc:
                result = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})
            if not result.get("thread", {}).get("id"):
                raise ValueError("Чат не найден")
        with self.state_lock:
            self.selected_thread_id = thread_id
            self._save_state()
        return {"threadId": thread_id}

    def delete_thread(self, thread_id: str) -> dict:
        if not isinstance(thread_id, str) or not VALID_THREAD_ID.fullmatch(thread_id):
            raise ValueError("Некорректный ID чата")
        with self.send_lock:
            with self.turn_lock:
                active = thread_id in self.active_turns
            with self.queue_lock:
                queued = any(item.get("threadId") == thread_id for item in self.queued_sends.values())
            if active or queued:
                raise RuntimeError("Дождитесь завершения задачи и сообщений в очереди")
            if thread_id in self.draft_threads:
                with self.state_lock:
                    self.draft_threads.pop(thread_id, None)
            else:
                if not any(item["id"] == thread_id for item in self.list_threads(limit=500)):
                    raise ValueError("Чат не найден")
                with self.rpc_session() as rpc:
                    self.controls.idle(rpc, thread_id)
                    rpc.call("thread/delete", {"threadId": thread_id})
            remaining = self.list_threads(limit=500)
            with self.state_lock:
                self.project_overrides.pop(thread_id, None)
                if self.selected_thread_id == thread_id:
                    if not remaining:
                        draft_id = "draft-" + uuid.uuid4().hex
                        self.draft_threads[draft_id] = str(Path.home())
                        self.selected_thread_id = draft_id
                    else:
                        self.selected_thread_id = remaining[0]["id"]
                self._save_state()
                selected = self.selected_thread_id
        return {"deleted": True, "selectedThreadId": selected}

    def create_thread(self, cwd: str | None = None) -> dict:
        project_id = None
        if cwd:
            path = Path(cwd).resolve()
            if not path.is_dir():
                raise ValueError("Рабочая папка проекта не найдена")
            # A new chat may use only a working directory already visible in this account.
            known = {item.get("cwd") for item in self.list_threads(limit=500)}
            with self.rpc_session() as rpc:
                project_page = rpc.call("project/list", {"limit": 100})
            for project in project_page.get("data", []):
                for root in project.get("roots", []):
                    root_path = root.get("path")
                    if isinstance(root_path, str):
                        known.add(str(Path(root_path).resolve()))
                        if str(path) == str(Path(root_path).resolve()): project_id = project.get("id")
            if str(path) not in known:
                raise ValueError("Проект не найден в списке чатов")
        else:
            if self.selected_thread_id in self.draft_threads:
                cwd = self.draft_threads[self.selected_thread_id]
            else:
                with self.rpc_session() as rpc:
                    current = rpc.call("thread/read", {"threadId": self.selected_thread_id, "includeTurns": False})
                cwd = current.get("thread", {}).get("cwd")
        thread_id = "draft-" + uuid.uuid4().hex
        with self.state_lock:
            self.draft_threads[thread_id] = cwd
            if project_id: self.project_overrides[thread_id] = project_id
            self.selected_thread_id = thread_id
            self._save_state()
        return {"threadId": thread_id}

    def outbox(self) -> list[dict]:
        result = []
        for path in sorted(self.outbox_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                continue
            file_id = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]
            result.append({"id": file_id, "name": path.name, "size": size,
                           "mimeType": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        return result

    def outbox_file(self, file_id: str) -> Path | None:
        for item in self.outbox():
            if hmac.compare_digest(item["id"], file_id):
                path = self.outbox_dir / item["name"]
                if path.is_file() and not path.is_symlink() and path.resolve().parent == self.outbox_dir:
                    return path
        return None

    def workspace_path(self, thread_id: str | None, relative: str = "") -> tuple[Path, Path]:
        thread_id = self._thread_id(thread_id)
        if relative.startswith("@chat-files/"):
            with self.chat_images_lock:
                linked = self.chat_files.get((thread_id, relative))
            if (linked is None or linked.is_symlink() or not linked.is_file()
                    or any(parent.is_symlink() for parent in linked.parents)):
                raise ValueError("Файл из ссылки недоступен. Обновите чат")
            return linked.parent, linked
        if thread_id in self.draft_threads:
            cwd = self.draft_threads[thread_id]
        else:
            with self.rpc_session() as rpc:
                result = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})
            cwd = result.get("thread", {}).get("cwd")
        if not cwd:
            raise ValueError("У этого чата нет рабочей папки")
        root = Path(cwd).resolve()
        if not root.is_dir():
            raise ValueError("Рабочая папка недоступна")
        if len(relative) > 1024 or Path(relative).is_absolute():
            raise ValueError("Некорректный путь")
        parts = Path(relative).parts if relative else ()
        if any(part in (".", "..") for part in parts):
            raise ValueError("Этот путь недоступен")
        path = root
        for part in parts:
            path = path / part
            if not path.resolve().is_relative_to(root):
                raise ValueError("Ссылка выходит за пределы проекта")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Путь выходит за пределы проекта")
        return root, path.resolve()

    def workspace_list(self, thread_id: str | None, relative: str = "", query: str = "", hidden: bool = False,
                       service: bool = False, cursor: str | None = None) -> dict:
        if relative.startswith("@chat-files/"):
            thread_id = self._thread_id(thread_id)
            with self.chat_images_lock:
                files = [(key, file) for (chat, key), file in self.chat_files.items()
                         if chat == thread_id and key.rsplit("/", 1)[0] == relative]
            entries = [{"name": file.name, "path": key, "isDirectory": False,
                        "size": file.stat().st_size} for key, file in files
                       if file.is_file() and not file.is_symlink()
                       and not any(parent.is_symlink() for parent in file.parents)]
            if not entries:
                raise ValueError("Файл из ссылки недоступен. Обновите чат")
            return {"rootName": "Файл из чата", "path": relative,
                    "entries": entries, "truncated": False}
        root, directory = self.workspace_path(thread_id, relative)
        if not directory.is_dir():
            raise ValueError("Папка не найдена")
        return listing(root, directory, relative, query, hidden, service, cursor)

    def workspace_resolve(self, thread_id: str | None, reference: str) -> dict:
        if not reference or len(reference) > 2048:
            raise ValueError("Некорректная ссылка на файл")
        reference = reference.strip()
        if reference.startswith("<") and reference.endswith(">"):
            reference = reference[1:-1]
        reference = unquote(reference.split("#", 1)[0].split("?", 1)[0])
        # Desktop file links may include a source line, e.g. /project/app.py:12.
        reference = re.sub(r":\d+(?::\d+)?$", "", reference)
        if reference.startswith("file://"):
            reference = reference[7:]
        thread_id = self._thread_id(thread_id)
        candidate = Path(reference)
        with self.chat_images_lock:
            key = next((key for (chat, key), file in self.chat_files.items()
                        if chat == thread_id and (key == reference or file == candidate)), None)
        if key is not None:
            _, linked = self.workspace_path(thread_id, key)
            if not linked.is_file():
                raise ValueError("Файл по ссылке не найден")
            return {"path": key, "folder": key.rsplit("/", 1)[0], "isDirectory": False, "absolutePath": str(linked)}
        root, _ = self.workspace_path(thread_id)
        if candidate.is_absolute():
            try:
                reference = str(candidate.relative_to(root))
            except ValueError as exc:
                thread_id = self._thread_id(thread_id)
                with self.chat_images_lock:
                    key = next((key for (chat, key), file in self.chat_files.items()
                                if chat == thread_id and file == candidate), None)
                if key is None:
                    raise ValueError("Файл вне проекта не найден среди ссылок текущего чата. Обновите чат") from exc
                return {"path": key, "folder": key.rsplit("/", 1)[0], "isDirectory": False}
        if reference.startswith("./"):
            reference = reference[2:]
        _, path = self.workspace_path(thread_id, reference)
        if not path.exists():
            raise ValueError("Файл по ссылке не найден")
        relative = str(path.relative_to(root))
        return {"path": relative, "folder": relative if path.is_dir()
                else ("" if path.parent == root else str(path.parent.relative_to(root))),
                "isDirectory": path.is_dir(), "absolutePath": str(path)}

    def register_chat_files(self, thread_id: str, text: str) -> None:
        # Grant access only to individual files explicitly linked in this chat.
        for reference in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            reference = reference.strip().removeprefix("<").removesuffix(">")
            reference = unquote(reference.split("#", 1)[0].split("?", 1)[0])
            reference = re.sub(r":\d+(?::\d+)?$", "", reference)
            reference = reference.removeprefix("file://")
            path = Path(reference)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                continue
            # Reject paths traversing symlinked directories as well.
            if any(parent.is_symlink() for parent in path.parents):
                continue
            path = path.resolve()
            file_id = hmac.new(self.token.encode(), (thread_id + str(path)).encode(),
                               hashlib.sha256).hexdigest()
            key = "@chat-files/" + file_id + "/" + path.name
            with self.chat_images_lock:
                self.chat_files[(thread_id, key)] = path

    def workspace_preview(self, thread_id: str | None, relative: str) -> dict:
        _, path = self.workspace_path(thread_id, relative)
        if not path.is_file():
            raise ValueError("Файл не найден")
        with path.open("rb") as source:
            data = source.read(128 * 1024 + 1)
        if b"\x00" in data:
            return {"path": relative, "previewable": False, "reason": "Двоичный файл"}
        try:
            decoder = codecs.getincrementaldecoder("utf-8")()
            text = decoder.decode(data[:128 * 1024], final=len(data) <= 128 * 1024)
        except UnicodeDecodeError:
            return {"path": relative, "previewable": False, "reason": "Текст не в UTF-8"}
        return {"path": relative, "previewable": True, "text": text,
                "truncated": len(data) > 128 * 1024}

    def register_chat_image(self, path_value: str) -> dict | None:
        original = Path(path_value)
        if original.is_symlink():
            return None
        path = original.resolve()
        if path.suffix.lower() not in IMAGE_MIME or not path.is_file():
            return None
        try:
            if path.stat().st_size > MAX_IMAGE_BYTES:
                return None
        except OSError:
            return None
        image_id = hmac.new(self.token.encode(), str(path).encode(), hashlib.sha256).hexdigest()
        with self.chat_images_lock:
            if len(self.chat_images) >= 1000:
                self.chat_images.clear()
            self.chat_images[image_id] = path
        return {"id": image_id, "name": path.name}

    def chat_image(self, image_id: str) -> Path | None:
        with self.chat_images_lock:
            return self.chat_images.get(image_id)

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text())
            if data.get("thread_id") == self.thread_id:
                return data
        except (FileNotFoundError, OSError, ValueError):
            pass
        return {}

    def _save_state(self) -> None:
        with self.state_lock:
            data = {
                "thread_id": self.thread_id,
                "selected_thread_id": self.selected_thread_id,
                "draft_threads": self.draft_threads,
                "project_overrides": self.project_overrides,
                "task_usage": self.task_usage,
                "token": self.token,
                "paired": self.paired,
                "guest_execution_enabled": getattr(self, "guest_execution_enabled", False),
                "pending": self.pending,
                "sent_messages": self.sent_messages,
                "send_operations": getattr(self, "send_operations", {}),
                "queued_sends": self.queued_sends,
                "thread_modes": self.thread_modes,
                "published_plans": self.published_plans,
                "control_operations": self.control_operations,
                "plan_boundaries": self.plan_boundaries,
                "cancelled_sends": self.cancelled_sends,
                "dismissed_sends": getattr(self, "dismissed_sends", {}),
                "last_queue_error": self.last_queue_error,
            }
            temporary = self.state_file.with_name(self.state_file.name + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(data, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, self.state_file)
            finally:
                temporary.unlink(missing_ok=True)

    def health(self, probe=False):
        if probe:
            with self.rpc_session() as rpc:
                rpc.call("model/list", {"limit": 1}, timeout=8)
        rpc = self.rpc
        connected = bool(getattr(rpc, "healthy", False)) if rpc is not None else self.last_rpc_health
        if rpc is not None and rpc.proc.poll() is not None:
            connected = False
        return {"bridgeReady": True, "codexReady": connected,
                "codexState": "idle" if rpc is None and connected else "ready" if connected else "unverified"}

    def capabilities(self) -> dict:
        now = time.monotonic()
        cached = getattr(self, "capability_cache", None)
        if cached and now - cached[0] < 60:
            return cached[1]
        with self.rpc_session() as rpc:
            def probe(method, params):
                try:
                    return True, rpc.call(method, params, timeout=8)
                except RpcError:
                    return False, {}
            modes_ok, modes_result = probe('collaborationMode/list', {})
            modes = modes_result.get('data', [])
            projects_ok, _ = probe('project/list', {'limit': 1})
            tid = self.selected_thread_id
            if tid in self.draft_threads:
                _, threads = probe('thread/list', {'limit': 1})
                tid = next((t.get('id') for t in threads.get('data', [])), None)
            queue_ok = goal_ok = search_ok = False
            if tid:
                queue_ok, _ = probe('thread/queue/list', {'threadId': tid, 'limit': 1})
                goal_ok, _ = probe('thread/goal/get', {'threadId': tid})
                search_ok, _ = probe('thread/searchOccurrences', {'threadId': tid, 'searchTerm': 'RemoteVibecode-capability-probe', 'limit': 1})
            executable = getattr(rpc, 'executable', '')
        transport = 'bundled-app-server' if 'resources' in Path(executable).parts else 'cli-app-server'
        result = {'protocolVersion': 1, 'agentVersion': '1.0.0',
                'desktop': {'transport': transport, 'modes': [m.get('mode') for m in modes]},
                'features': {'workspacePaging': True, 'workspaceSearch': True, 'workspaceVisibility': True,
                    'controls': modes_ok and projects_ok and queue_ok and goal_ok and search_ok,
                    'projects': projects_ok, 'historySearch': search_ok, 'goal': goal_ok,
                    'queueReconciliation': queue_ok, 'planCreate': any(m.get('mode') == 'plan' for m in modes),
                    'nativeSteer': False},
                'limitations': {'nativeSteer': 'Desktop не предоставляет подтверждённого атомарного переноса очереди в корректировку'}}
        if tid: self.capability_cache = (now, result)
        return result

    def reconcile_native_queue(self, tid, rpc, snapshot=None):
        with self.queue_lock:
            if not any(m.get("threadId") == tid and (m.get("nativeId") or m.get("nativePending")) for m in self.queued_sends.values()):
                return
        snapshot = snapshot or rpc.call('thread/queue/list', {'threadId': tid, 'limit': 100})
        entries = list(snapshot.get('data', []))
        cursor = snapshot.get('nextCursor')
        while cursor and len(entries) < 5000:
            page = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 100, 'cursor': cursor})
            entries.extend(page.get('data', []))
            next_cursor = page.get('nextCursor')
            if next_cursor == cursor:
                break
            cursor = next_cursor
        native = {m.get('id'): m for m in entries}
        thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': True}).get('thread', {})
        delivered = {i.get('clientId'): t.get('id') for t in thread.get('turns', [])
                     for i in t.get('items', []) if i.get('type') == 'userMessage' and i.get('clientId')}
        with self.queue_lock, self.state_lock:
            changed = False
            for mid, msg in list(self.queued_sends.items()):
                if msg.get('threadId') != tid or not (msg.get('nativeId') or msg.get('nativePending')):
                    continue
                match = next((item for item in entries if item.get('clientUserMessageId') == mid), None)
                if match and not msg.get('nativeId'):
                    msg['nativeId'] = match['id']
                    msg['nativePending'] = False
                    changed = True
                if mid in delivered:
                    changed = True
                    self.sent_messages[mid] = {'threadId': tid, 'turnId': delivered[mid], 'clientMessageId': mid}
                    self.queued_sends.pop(mid, None)
                    for fid in msg.get('files', []): self.pending.pop(fid, None)
                elif msg.get('nativeId') in native:
                    item = native[msg['nativeId']]
                    text = '\n'.join(i.get('text', '') for i in item.get('input', []) if i.get('type') == 'text')
                    changed |= msg.get('queueState') != 'queued' or msg.get('text') != text
                    msg['queueState'] = 'queued'
                    msg['text'] = text
                elif not cursor:
                    changed |= msg.get('queueState') != 'checking'
                    msg['queueState'] = 'checking'
            while len(self.sent_messages) > 512: self.sent_messages.pop(next(iter(self.sent_messages)))
            if changed: self._save_state()

    def status(self, thread_id: str | None = None) -> dict:
        thread_id = self._thread_id(thread_id)
        if thread_id in self.draft_threads:
            return {"threadId": thread_id, "title": "Новый чат",
                    "cwd": self.draft_threads[thread_id], "projectId": None,
                    "model": None, "reasoningEffort": None, "status": "draft",
                    "codexConnected": True, "queuedCount": 0, "queueError": "",
                    "oldestQueuedSeconds": 0}
        with self.rpc_session() as rpc:
            result = rpc.call(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
        thread = result.get("thread", {})
        for turn in thread.get("turns") or []:
            self._remember_agent_relations(thread_id, turn.get("items") or [])
        active = next((t for t in reversed(thread.get("turns") or []) if t.get("status") == "inProgress"), None)
        state = thread.get("status") or {}
        with self.queue_lock:
            queued = [item for item in self.queued_sends.values()
                      if (item.get("threadId") or self.thread_id) == thread_id]
            queued_count = len(queued)
            oldest_queued_seconds = int(time.time() - min(
                (item.get("queuedAt", time.time()) for item in queued),
                default=time.time(),
            ))
        return {
            "threadId": thread_id,
            "title": thread.get("name") or thread.get("preview") or "Задача Codex",
            "cwd": thread.get("cwd"),
            "projectId": thread.get("projectId"),
            "model": thread.get("model"),
            "reasoningEffort": thread.get("reasoningEffort"),
            "status": state.get("type", "unknown") if isinstance(state, dict) else state,
            "codexConnected": True,
            "activeTurnId": active.get("id", "") if active else "",
            "pendingRequestCount": len(self.requests(thread_id)["requests"]),
            "queuedCount": queued_count,
            "queueError": self.last_queue_error if queued_count else "",
            "oldestQueuedSeconds": oldest_queued_seconds,
        }

    def agent_activity_label(self, item: dict, fetch: bool = True) -> str:
        agent_id = item.get("agentThreadId")
        now = time.monotonic()
        with self.agent_labels_lock:
            cached = self.agent_labels.get(agent_id)
        detail = cached[1] if cached else {}
        if fetch and agent_id and (not cached or now - cached[0] > 300):
            try:
                with self.rpc_session() as rpc:
                    detail = rpc.call("thread/read", {"threadId": agent_id,
                                                       "includeTurns": False}).get("thread", {})
            except (RpcError, RuntimeError, queue.Empty):
                detail = {}
            with self.agent_labels_lock:
                if len(self.agent_labels) >= 500:
                    self.agent_labels.clear()
                self.agent_labels[agent_id] = (now, detail)
        name = detail.get("agentNickname") or detail.get("name")
        if not name:
            name = str(item.get("agentPath") or "").rsplit("/", 1)[-1].replace("_", " ")
        parts = [str(name or "Субагент")[:80]]
        for key in ("agentRole", "model"):
            if detail.get(key):
                parts.append(str(detail[key])[:80])
        prefix = {"completed": "Завершил работу", "spawned": "Создан"}.get(
            item.get("kind"), "Субагент")
        return "Субагент" if parts == ["Субагент"] and prefix == "Субагент" else prefix + " · " + " · ".join(parts)

    def history(self, thread_id: str | None = None, before: str | None = None,
                limit: int = 60, around: str | None = None) -> dict:
        thread_id = self._thread_id(thread_id)
        limit = max(1, min(int(limit), 120))
        if thread_id in self.draft_threads:
            return {"threadId": thread_id, "turns": [], "hasMore": False, "nextBefore": None}
        with self.rpc_session() as rpc:
            result = rpc.call(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
        thread = result.get("thread", {})
        recent_agents = set()
        for recent_turn in reversed(thread.get("turns") or []):
            for recent_item in reversed(recent_turn.get("items") or []):
                if recent_item.get("type") == "subAgentActivity":
                    if len(recent_agents) < 12:
                        recent_agents.add(recent_item.get("agentThreadId"))
            if len(recent_agents) >= 12:
                break
        turns = []
        # Desktop-owned turns may be seen by polling before notifications arrive.
        # Only observe the latest running task, never invent baselines for old tasks.
        latest = (thread.get("turns") or [])[-1:]
        for observed in latest:
            observed_id = str(observed.get("id") or "")
            with self.state_lock:
                record = self.task_usage.get(observed_id, {})
                needs_start = observed.get("status") == "inProgress" and "before" not in record
                needs_end = observed.get("status") in ("completed", "interrupted", "failed") and "before" in record and "after" not in record
                if observed_id and (needs_start or needs_end) and not record.get("observationQueued"):
                    try:
                        self.usage_events.put_nowait(("turn/started" if needs_start else "turn/completed",
                            {"threadId": thread_id, "turn": observed}))
                        self.task_usage.setdefault(observed_id, {})["observationQueued"] = True
                    except queue.Full:
                        pass
        for turn in thread.get("turns", []):
            turn_id = str(turn.get("id") or "")
            started_at = turn.get("startedAt")
            completed_at = turn.get("completedAt")
            summaries = []
            activities = []
            messages = []
            generated_images = []
            for item_index, item in enumerate(turn.get("items", [])):
                item_type = item.get("type")
                if item_type == "userMessage":
                    content = item.get("content") or []
                    text = "".join(
                        part.get("text", "")
                        for part in content
                        if part.get("type") in ("text", "input_text")
                    )
                    attachments = [Path(str(part.get("path") or "")).name
                                   for part in content if isinstance(part, dict)
                                   and part.get("type") in ("localImage", "localFile") and part.get("path")]
                    images = [image for part in content if isinstance(part, dict)
                              and part.get("type") == "localImage" and part.get("path")
                              if (image := self.register_chat_image(str(part["path"]))) is not None]
                    self.register_chat_files(thread_id, text)
                    if "Переданные файлы (прочитай по этим путям):" in text:
                        attachments.extend(re.findall(r"(?m)^- ([^\n:]+): /[^\n]+$", text))
                        images.extend(image for path in re.findall(r"(?m)^- [^\n:]+: (/[^\n]+)$", text)
                                      if (image := self.register_chat_image(path)) is not None)
                        text = text.split("Переданные файлы (прочитай по этим путям):", 1)[0].rstrip()
                    if text or attachments or images:
                        turns.append({"id": f"{turn_id}:user:{item_index}", "role": "user",
                                      "text": text, "clientMessageId": item.get("clientId"), "attachments": attachments[:20], "images": images[:20],
                                      "time": started_at, "turnId": turn_id})
                elif item_type == "agentMessage":
                    text = item.get("text", "")
                    self.register_chat_files(thread_id, text)
                    if text:
                        images = [image for path in re.findall(r"!\[[^\]]*\]\((/[^)]+)\)", text)
                                  if (image := self.register_chat_image(path)) is not None]
                        images.extend(generated_images)
                        generated_images = []
                        messages.append({"id": f"{turn_id}:assistant:{item_index}",
                                         "role": "assistant", "text": text, "time": completed_at or started_at,
                                         "phase": item.get("phase"), "turnId": turn_id,
                                         "images": images[:20]})
                elif item_type == "imageGeneration" and item.get("status") == "completed":
                    path = item.get("savedPath")
                    image = self.register_chat_image(str(path)) if path else None
                    if image is not None:
                        generated_images.append(image)
                elif item_type == "collabAgentToolCall":
                    label = {"spawnAgent": "Создан субагент", "sendInput": "Уточнение субагенту",
                             "sendMessage": "Сообщение субагенту", "followupTask": "Новая задача субагенту",
                             "interruptAgent": "Остановка субагента", "closeAgent": "Закрытие субагента",
                             "wait": "Ожидание субагентов"}.get(item.get("tool"), "Действие с субагентами")
                    activities.append({"kind": "tool", "label": label,
                                       "status": item.get("status") or "completed"})
                elif item_type == "subAgentActivity":
                    activities.append({"kind": "tool", "label": self.agent_activity_label(item, item.get("agentThreadId") in recent_agents),
                                       "status": "completed"})
                elif item_type == "reasoning":
                    summary = item.get("summary") or []
                    if isinstance(summary, list):
                        summaries.extend(part[:500] for part in summary if isinstance(part, str) and part.strip())
                elif item_type == "commandExecution":
                    actions = item.get("commandActions") or []
                    if not isinstance(actions, list) or not actions:
                        actions = [{"type": "unknown", "command": item.get("command", "")}]
                    for action in actions[:8]:
                        if not isinstance(action, dict):
                            continue
                        kind = action.get("type")
                        path = Path(str(action.get("path") or action.get("name") or "")).name
                        if kind == "read":
                            label = "Прочитан файл" + (f" {path}" if path else "")
                        elif kind == "listFiles":
                            label = "Просмотрены файлы" + (f" в {path}" if path else "")
                        elif kind == "search":
                            label = "Поиск по файлам"
                        else:
                            command = str(action.get("command") or item.get("command") or "").splitlines()[0].strip()
                            safe = command.startswith((
                                "adb shell input tap ", "adb shell input swipe ",
                                "adb shell input keyevent ", "adb shell screencap ",
                                "adb pull ", "git status", "git diff --stat", "rg --files",
                            )) and not re.search(
                                r"(?i)token|password|secret|bearer|authorization|pairing|--pin", command)
                            label = ("Выполнена команда " + command[:90]) if safe else "Выполнена команда"
                        activities.append({"kind": "command", "label": label[:120],
                                           "status": item.get("status") or "completed"})
                elif item_type == "fileChange":
                    changes = item.get("changes") or []
                    for change in changes[:8] if isinstance(changes, list) else []:
                        if isinstance(change, dict):
                            name = Path(str(change.get("path") or "")).name
                            activities.append({"kind": "file", "label": "Изменён файл" +
                                               (f" {name}" if name else ""),
                                               "status": item.get("status") or "completed"})
                elif item_type == "imageView":
                    name = Path(str(item.get("path") or "")).name
                    activities.append({"kind": "image", "label": "Просмотрено изображение" +
                                       (f" {name}" if name else ""), "status": "completed"})
                elif item_type == "webSearch":
                    activities.append({"kind": "web", "label": "Поиск в интернете", "status": "completed"})
                elif item_type == "mcpToolCall":
                    activities.append({"kind": "tool", "label": "Вызван инструмент " +
                                       str(item.get("tool") or "")[:60],
                                       "status": item.get("status") or "completed"})
                elif item_type == "plan" and item.get("text"):
                    summaries.append("План: " + str(item["text"])[:500])
            if generated_images:
                if messages:
                    messages[-1].setdefault("images", []).extend(generated_images)
                else:
                    messages.append({"id": f"{turn_id}:generated", "role": "assistant",
                                     "text": "", "images": generated_images[:20],
                                     "time": completed_at or started_at, "turnId": turn_id})
            turns.append({"id": f"{turn_id}:started", "role": "system", "text": "Codex начал работу",
                          "time": started_at, "turnId": turn_id})
            with self.state_lock:
                notices = list(self.task_usage.get(turn_id, {}).get("notices", []))
            for index, notice in enumerate(notices):
                turns.append({"id": f"{turn_id}:notice:{index}", "role": "system", "text": notice,
                              "time": started_at, "turnId": turn_id})
            if summaries or activities:
                turns.append({"id": f"{turn_id}:process", "role": "process",
                              "text": "\n".join(summaries[-3:]), "time": started_at,
                              "activities": activities[-30:],
                              "turnId": turn_id, "steps": len(activities)})
            elif turn.get("status") == "inProgress":
                turns.append({"id": f"{turn_id}:process", "role": "process",
                              "text": "Codex работает…", "time": started_at,
                              "turnId": turn_id, "steps": 0})
            turns.extend(messages)
            outcome = turn.get("status")
            outcome_text = {
                "completed": "Работа Codex завершена",
                "interrupted": "Работа Codex прервана",
                "failed": "Работа Codex завершилась с ошибкой",
            }.get(outcome)
            if outcome_text:
                outcome_summary, quota_summary = self.task_outcome_summary(turn)
                turns.append({"id": f"{turn_id}:outcome", "role": "outcome",
                              "outcomeSummary": outcome_summary, "quotaSummary": quota_summary,
                              "text": outcome_text + "\n" + self.task_usage_text(turn_id) +
                                  ("\n" + str((turn.get("error") or {}).get("message", ""))[:500] if outcome == "failed" else ""),
                              "time": completed_at or started_at,
                              "turnId": turn_id})
        end = next((index for index, item in enumerate(turns) if item["id"] == before), len(turns)) if before else len(turns)
        if around:
            index = next((i for i, item in enumerate(turns) if item.get("turnId") == around), None)
            if index is None:
                raise ValueError("Сообщение не найдено в истории")
            end = min(len(turns), index + limit // 2)
        start = max(0, end - limit)
        page = turns[start:end]
        return {"threadId": thread_id, "turns": page, "hasMore": start > 0,
                "existingTurnIds": [t.get("id") for t in thread.get("turns", [])],
                "existingMessageIds": [item["id"] for item in turns],
                "nextBefore": page[0]["id"] if page and start > 0 else None}

    def upload(self, original_name: str, content_type: str, stream, length: int,
               expected_sha256: str = "", client_upload_id: str = "") -> dict:
        if length < 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("Файл превышает ограничение 40 МБ")
        if client_upload_id and not re.fullmatch(r"[0-9a-f]{32}", client_upload_id):
            raise ValueError("Некорректный ID загрузки")
        if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("Некорректная контрольная сумма файла")
        with self.upload_lock:
            with self.state_lock:
                existing = self.pending.get(client_upload_id) if client_upload_id else None
            if existing is not None:
                if existing.get("size") != length or (expected_sha256 and existing.get("sha256") != expected_sha256.lower()):
                    raise ValueError("ID загрузки уже занят другим файлом")
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Передача оборвалась до конца файла")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if not hmac.compare_digest(digest.hexdigest(), existing["sha256"]):
                    raise ValueError("Контрольная сумма файла не совпала")
                return existing
            return self._upload_once(original_name, content_type, stream, length,
                                     expected_sha256, client_upload_id)

    def _upload_once(self, original_name: str, content_type: str, stream, length: int,
                     expected_sha256: str, client_upload_id: str) -> dict:
        if length < 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError(f"Файл превышает ограничение {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
        if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("Некорректная контрольная сумма файла")
        basename = Path(original_name.replace("\\", "/")).name
        safe = SAFE_NAME.sub("_", basename).strip(" .")[:100] or "file"
        file_id = client_upload_id or uuid.uuid4().hex
        target = self.upload_dir / f"{file_id}_{safe}"
        temporary = self.upload_dir / f".{file_id}.part"
        written = 0
        digest = hashlib.sha256()
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                while written < length:
                    chunk = stream.read(min(64 * 1024, length - written))
                    if not chunk:
                        raise ValueError("Передача оборвалась до конца файла")
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            actual_sha256 = digest.hexdigest()
            if expected_sha256 and not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
                raise ValueError("Контрольная сумма файла не совпала")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        mime = content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
        item = {"id": file_id, "name": safe, "path": str(target), "mimeType": mime, "size": written, "sha256": digest.hexdigest()}
        item["createdAt"] = time.time()
        with self.state_lock:
            self.pending[file_id] = item
            self._save_state()
        return item

    def _finish_send(self, response, file_ids, draft_id=None):
        thread_id = response["threadId"]
        mid = response["clientMessageId"]
        with self.state_lock:
            if draft_id:
                self.draft_threads.pop(draft_id, None)
                if draft_id in self.project_overrides:
                    self.project_overrides[thread_id] = self.project_overrides.pop(draft_id)
                if self.selected_thread_id == draft_id:
                    self.selected_thread_id = thread_id
            for file_id in file_ids:
                self.pending.pop(file_id, None)
            self.sent_messages[mid] = response
            getattr(self, "send_operations", {}).pop(mid, None)
            while len(self.sent_messages) > 512:
                self.sent_messages.pop(next(iter(self.sent_messages)))
            self._save_state()
        return response

    def send(self, text: str, file_ids: list[str], client_message_id: str,
             thread_id: str | None = None, model: str | None = None,
             effort: str | None = None, collaboration_mode: str | None = None,
             require_idle: bool = False) -> dict:
        thread_id = self._thread_id(thread_id)
        with self.send_lock:
            if time.monotonic() < self.update_until:
                raise ValueError("Агент устанавливает обновление. Повторите отправку после подключения.")
            if client_message_id in getattr(self, "dismissed_sends", {}):
                raise ValueError("Исход сообщения скрыт локально. Повторный запуск с этим ID запрещён")
            if client_message_id in self.cancelled_sends:
                raise ValueError("Сообщение отменено")
            if client_message_id in self.sent_messages:
                return self.sent_messages[client_message_id]
            if getattr(self, "guest_runtime", None) is not None and self.guest_runtime.busy.is_set():
                raise BusyError("Гостевая задача выполняется; сообщение владельца поставлено в очередь")
            fingerprint = hashlib.sha256(json.dumps({"text": text, "files": file_ids,
                "threadId": thread_id, "model": model, "effort": effort,
                "mode": collaboration_mode}, sort_keys=True).encode()).hexdigest()
            if not hasattr(self, "send_operations"):
                self.send_operations = {}
            operation = self.send_operations.get(client_message_id)
            if operation and operation["fingerprint"] != fingerprint:
                raise ValueError("ID сообщения уже использован для другого запроса")
            if operation and operation.get("threadId"):
                thread_id = operation["threadId"]
            if operation and operation.get("stage") == "thread-starting" and not operation.get("threadId"):
                raise RuntimeError("Создание чата имеет неизвестный исход. Проверьте Desktop; запрос не запущен повторно")
            with self.turn_lock:
                if thread_id in self.active_turns and not operation:
                    raise BusyError("Задача выполняется")
            with self.state_lock:
                missing = [file_id for file_id in file_ids if file_id not in self.pending]
                if missing:
                    raise ValueError("Одно из вложений не найдено; передай файл заново")
            if not text.strip() and not file_ids:
                raise ValueError("Добавь текст или файл")
            if model or effort:
                available = self.models()["models"]
                if model:
                    chosen = next((item for item in available if item["id"] == model), None)
                    if chosen is None:
                        raise ValueError("Модель недоступна")
                else:
                    if thread_id in self.draft_threads:
                        model = available[0]["id"] if available else None
                    else:
                        with self.rpc_session() as rpc:
                            current = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})
                        model = current.get("thread", {}).get("model")
                    chosen = next((item for item in available if item["id"] == model), None)
                if effort and (chosen is None or effort not in chosen["efforts"]):
                    raise ValueError("Усилие недоступно для этой модели")
            with self.rpc_session() as rpc:
                draft_id = (operation or {}).get("draftId") or (thread_id if thread_id in self.draft_threads else None)
                if operation is None:
                    operation = {"fingerprint": fingerprint, "draftId": draft_id, "stage": "prepared"}
                    with self.state_lock:
                        if len(self.send_operations) >= 100:
                            raise ValueError("Слишком много неподтверждённых отправок. Проверьте их исход в Desktop")
                        self.send_operations[client_message_id] = operation
                        self._save_state()
                if draft_id and not operation.get("threadId"):
                    cwd = self.draft_threads[draft_id]
                    start_params = {"serviceName": "codex_phone_companion"}
                    if cwd:
                        start_params["cwd"] = cwd
                    draft_project = self.project_overrides.get(draft_id)
                    if draft_project:
                        start_params["projectId"] = draft_project
                    with self.state_lock:
                        operation["stage"] = "thread-starting"
                        self._save_state()
                    try:
                        created = rpc.call("thread/start", start_params)
                    except RpcError:
                        with self.state_lock:
                            self.send_operations.pop(client_message_id, None)
                            self._save_state()
                        raise
                    thread_id = created.get("thread", {}).get("id")
                    if not thread_id:
                        raise RuntimeError("Codex не создал чат")
                    with self.state_lock:
                        operation.update(threadId=thread_id, stage="prepared")
                        self._save_state()
                else:
                    existing = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True}).get("thread", {})
                    delivered = next((t for t in existing.get("turns", []) if any(
                        i.get("type") == "userMessage" and i.get("clientId") == client_message_id for i in t.get("items", []))), None)
                    if delivered:
                        response = {"threadId": thread_id, "turnId": delivered["id"], "clientMessageId": client_message_id}
                        return self._finish_send(response, file_ids, draft_id)
                    if operation.get("stage") == "turn-starting":
                        raise RuntimeError("Исход отправки ещё не подтверждён Desktop. Проверяю историю; повторный запуск исключён")
                    if require_idle:
                        self.controls.idle(rpc, thread_id)
                    # Existing chats must be resumed before another client writes to them.
                    try:
                        rpc.call("thread/resume", {"threadId": thread_id}, timeout=15)
                    except RpcError as exc:
                        if "active writer" in str(exc).lower() or "already active" in str(exc).lower():
                            raise BusyError("Задача сейчас отвечает. Сообщение осталось в поле ввода; отправь ещё раз после ответа.") from exc
                        raise

                with self.state_lock:
                    attachments = [self.pending[file_id] for file_id in file_ids]
                input_items = []
                path_files = []
                for item in attachments:
                    if item["mimeType"].startswith("image/"):
                        input_items.append({"type": "localImage", "path": item["path"]})
                    else:
                        path_files.append(item)
                final_text = text.strip()
                if path_files:
                    listing = "\n".join(f"- {f['name']}: {f['path']}" for f in path_files)
                    final_text = (final_text + "\n\n" if final_text else "") + "Переданные файлы (прочитай по этим путям):\n" + listing
                if final_text:
                    input_items.append({"type": "text", "text": final_text})
                if not input_items:
                    raise ValueError("Добавь текст или файл")
                params = {"threadId": thread_id, "input": input_items,
                          "clientUserMessageId": client_message_id}
                if model:
                    params["model"] = model
                if effort:
                    params["effort"] = effort
                mode = collaboration_mode or self.thread_modes.get(thread_id)
                if mode:
                    thread_info = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False}).get("thread", {})
                    effective_model = model or thread_info.get("model")
                    if not effective_model:
                        raise ValueError("Для режима работы выберите модель")
                    params["collaborationMode"] = {"mode": mode, "settings": {"model": effective_model,
                        "reasoning_effort": effort or thread_info.get("reasoningEffort"), "developer_instructions": None}}
                try:
                    try:
                        usage_before = self.usage_limits()
                    except (RpcError, RuntimeError, OSError, queue.Empty):
                        usage_before = None
                    with self.state_lock:
                        operation.update(threadId=thread_id, stage="turn-starting")
                        self._save_state()
                    result = rpc.call("turn/start", params)
                except RpcError as exc:
                    with self.state_lock:
                        operation["stage"] = "prepared"  # Explicit rejection is safe to retry.
                        self._save_state()
                    if any(marker in str(exc).lower() for marker in
                           ("active turn", "turn is active", "already active", "turn in progress")):
                        raise BusyError("Задача выполняется") from exc
                    raise
                turn_id = (result.get("turn") or {}).get("id")
                if turn_id and usage_before:
                    with self.state_lock:
                        self.task_usage.setdefault(turn_id, {"threadId": thread_id})["before"] = usage_before
                        self.task_usage[turn_id].pop("baselineObserved", None)
                        self._save_state()
                if turn_id:
                    with self.turn_lock:
                        self.active_turns[thread_id] = turn_id
            response = {"turnId": (result.get("turn") or {}).get("id"),
                        "threadId": thread_id, "clientMessageId": client_message_id}
            return self._finish_send(response, file_ids, draft_id)

    def submit(self, text: str, file_ids: list[str], client_message_id: str,
               thread_id: str | None = None, model: str | None = None,
               effort: str | None = None) -> dict:
        if not hasattr(self, "submission_lock"):
            self.submission_lock = threading.Lock()
        with self.submission_lock:
            return Bridge._submit_locked(self, text, file_ids, client_message_id, thread_id, model, effort)

    def _submit_locked(self, text, file_ids, client_message_id, thread_id, model, effort):
        thread_id = self._thread_id(thread_id)
        if client_message_id in self.sent_messages:
            return self.sent_messages[client_message_id]
        if client_message_id in self.cancelled_sends:
            raise ValueError("Сообщение отменено")
        with self.queue_lock:
            if client_message_id in self.queued_sends:
                return {"queued": True, "threadId": thread_id, "clientMessageId": client_message_id,
                        "nativeSubmissionId": self.queued_sends[client_message_id].get("nativeId") or ""}
        try:
            return self.send(text, file_ids, client_message_id, thread_id, model, effort)
        except BusyError:
            if not text.strip() and not file_ids:
                raise ValueError("Добавь текст или файл")
            with self.state_lock:
                if any(file_id not in self.pending for file_id in file_ids):
                    raise ValueError("Одно из вложений не найдено; передай файл заново")
            native_id = None
            use_native = not (getattr(self, "guest_runtime", None) and self.guest_runtime.busy.is_set()) and not model and not effort and not self.thread_modes.get(thread_id) and thread_id not in self.draft_threads
            # Reserve/persist BEFORE queue/add. Unknown replies are reconciled, never re-added.
            with self.queue_lock, self.state_lock:
                if len(self.queued_sends) >= 50:
                    raise ValueError("Очередь заполнена; дождись доставки предыдущих сообщений")
                self.queued_sends[client_message_id] = {
                    "text": text, "files": file_ids, "clientMessageId": client_message_id,
                    "threadId": thread_id, "model": model, "effort": effort,
                    "queuedAt": time.time(), "nativeId": None, "nativePending": use_native,
                    "queueState": "checking" if use_native else "queued",
                }
                self._save_state()
            # Native queue has no per-message model/effort overrides. Preserve explicit overrides
            # in the bridge queue rather than silently changing their meaning.
            if use_native:
                with self.send_lock, self.rpc_session() as rpc:
                    thread = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True}).get("thread", {})
                    delivered = next((t for t in thread.get("turns", []) if any(
                        i.get("type") == "userMessage" and i.get("clientId") == client_message_id for i in t.get("items", []))), None)
                    if delivered:
                        response = {"threadId": thread_id, "turnId": delivered["id"], "clientMessageId": client_message_id}
                        with self.state_lock:
                            self.sent_messages[client_message_id] = response
                            while len(self.sent_messages) > 512: self.sent_messages.pop(next(iter(self.sent_messages)))
                            for file_id in file_ids: self.pending.pop(file_id, None)
                            self._save_state()
                        with self.queue_lock, self.state_lock:
                            self.queued_sends.pop(client_message_id, None)
                            self._save_state()
                        return response
                    existing = rpc.call("thread/queue/list", {"threadId": thread_id, "limit": 100}).get("data", [])
                    prior = next((q for q in existing if q.get("clientUserMessageId") == client_message_id), None)
                    if prior:
                        native_id = prior["id"]
                    else:
                        with self.state_lock:
                            attachments = [self.pending.get(k) for k in file_ids]
                        if any(a is None for a in attachments):
                            raise ValueError("Вложение не найдено")
                        inputs = [{"type": "localImage", "path": a["path"]} for a in attachments if a["mimeType"].startswith("image/")]
                        paths = [a for a in attachments if not a["mimeType"].startswith("image/")]
                        message = text
                        if paths:
                            message += "\n\nПереданные файлы (прочитай по этим путям):\n" + "\n".join(f"- {a['name']}: {a['path']}" for a in paths)
                        if message.strip(): inputs.append({"type": "text", "text": message})
                        try:
                            result = rpc.call("thread/queue/add", {"threadId": thread_id, "input": inputs, "clientUserMessageId": client_message_id})
                        except RpcError:
                            with self.queue_lock, self.state_lock:
                                self.queued_sends.pop(client_message_id, None)
                                self._save_state()
                            raise
                        native_id = result.get("queuedSubmission", {}).get("id")
                        if not native_id:
                            raise RuntimeError("Desktop не вернул идентификатор сообщения очереди")
            with self.queue_lock:
                with self.state_lock:
                    if client_message_id in self.cancelled_sends:
                        raise ValueError("Сообщение отменено")
                    self.queued_sends[client_message_id] = {
                        "text": text,
                        "files": file_ids,
                        "clientMessageId": client_message_id,
                        "threadId": thread_id,
                        "model": model,
                        "effort": effort,
                        "queuedAt": time.time(),
                        "nativeId": native_id,
                        "nativePending": False,
                        "queueState": "queued",
                    }
                    self.last_queue_error = ""
                    self._save_state()
            return {"queued": True, "threadId": thread_id, "clientMessageId": client_message_id, "nativeSubmissionId": native_id or ""}

    def dismiss_unknown_message(self, mid, confirmed=False):
        if not confirmed:
            raise ValueError("Подтвердите скрытие: это не отменяет задачу в Desktop")
        with self.send_lock, self.queue_lock, self.state_lock:
            msg = self.queued_sends.get(mid)
            if mid in self.sent_messages:
                return {"status": "sent"}
            if not hasattr(self, "dismissed_sends"):
                self.dismissed_sends = {}
            if mid in self.dismissed_sends:
                return {"status": "dismissed"}
            if not msg or msg.get("queueState") != "checking":
                raise ValueError("Можно скрыть только сообщение с неизвестным исходом")
            self.dismissed_sends[mid] = {"threadId": msg["threadId"], "at": time.time()}
            self.queued_sends.pop(mid, None)
            self._save_state()
        return {"status": "dismissed", "note": "Скрыто на телефоне; выполнение или отмена Desktop не подтверждены"}

    def cancel_message(self, client_message_id: str) -> dict:
        with self.send_lock:
            with self.queue_lock:
                message = self.queued_sends.get(client_message_id)
            if message and message.get("nativeId"):
                with self.rpc_session() as rpc:
                    self.reconcile_native_queue(message["threadId"], rpc)
                    if client_message_id in self.sent_messages:
                        return {"status": "sent"}
                    if message.get("queueState") == "checking":
                        raise ValueError("Запись исчезла из очереди Desktop. Её исход проверяется; отмена не подтверждена")
                    rpc.call("thread/queue/delete", {"threadId": message["threadId"], "queuedSubmissionId": message["nativeId"]})
            with self.queue_lock:
                with self.state_lock:
                    if client_message_id in self.sent_messages:
                        return {"status": "sent"}
                    self.queued_sends.pop(client_message_id, None)
                    self.cancelled_sends[client_message_id] = time.time()
                    while len(self.cancelled_sends) > 512:
                        self.cancelled_sends.pop(next(iter(self.cancelled_sends)))
                    self._save_state()
                    return {"status": "cancelled"}

    def steer_queued(self, client_message_id: str) -> dict:
        with self.send_lock:
            if time.monotonic() < self.update_until:
                raise ValueError("Агент устанавливает обновление. Повторите отправку после подключения.")
            if client_message_id in self.sent_messages:
                return self.sent_messages[client_message_id]
            with self.queue_lock:
                message = self.queued_sends.get(client_message_id)
            if message is None:
                raise ValueError("Сообщение уже вышло из очереди или отменено")
            if message.get("nativeId"):
                raise ValueError("Это очередь Desktop: откройте Инструменты чата → Очередь Desktop")
            thread_id = self._thread_id(message.get("threadId"))
            with self.state_lock:
                attachments = [self.pending.get(file_id) for file_id in message.get("files", [])]
            if any(item is None for item in attachments):
                raise ValueError("Вложение больше недоступно")
            input_items = [
                {"type": "localImage", "path": item["path"]}
                for item in attachments if item["mimeType"].startswith("image/")
            ]
            path_files = [item for item in attachments if not item["mimeType"].startswith("image/")]
            final_text = str(message.get("text") or "").strip()
            if path_files:
                listing = "\n".join(f"- {item['name']}: {item['path']}" for item in path_files)
                final_text = (final_text + "\n\n" if final_text else "") + "Переданные файлы (прочитай по этим путям):\n" + listing
            if final_text:
                input_items.append({"type": "text", "text": final_text})
            if not input_items:
                raise ValueError("Пустое сообщение")
            with self.rpc_session() as rpc:
                with self.turn_lock:
                    active_turn_id = self.active_turns.get(thread_id)
                if not active_turn_id:
                    result = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})
                    turns = result.get("thread", {}).get("turns") or []
                    for turn in reversed(turns):
                        state = turn.get("status") or {}
                        kind = state.get("type") if isinstance(state, dict) else state
                        if kind in ("inProgress", "active", "running"):
                            active_turn_id = turn.get("id")
                            break
                if not active_turn_id:
                    raise ValueError("Сейчас нет активного хода для корректировки")
                result = rpc.call("turn/steer", {"threadId": thread_id,
                    "expectedTurnId": active_turn_id, "input": input_items,
                    "clientUserMessageId": client_message_id})
            response = {"turnId": result.get("turnId") or active_turn_id,
                        "threadId": thread_id, "clientMessageId": client_message_id,
                        "steered": True}
            with self.queue_lock:
                with self.state_lock:
                    self.queued_sends.pop(client_message_id, None)
                    for file_id in message.get("files", []):
                        self.pending.pop(file_id, None)
                    self.sent_messages[client_message_id] = response
                    if len(self.sent_messages) > 512:
                        self.sent_messages.pop(next(iter(self.sent_messages)))
                    self._save_state()
            return response

    def cancel_upload(self, file_id: str) -> dict:
        with self.upload_lock:
            with self.send_lock:
                with self.queue_lock:
                    with self.state_lock:
                        if any(file_id in queued.get("files", []) for queued in self.queued_sends.values()):
                            return {"deleted": False, "queued": True}
                        item = self.pending.pop(file_id, None)
                        if item is not None:
                            Path(item["path"]).unlink(missing_ok=True)
                            self._save_state()
        return {"deleted": item is not None}

    def cleanup_uploads(self) -> None:
        now = time.time()
        with self.send_lock:
            with self.queue_lock:
                with self.state_lock:
                    used = {file_id for queued in self.queued_sends.values()
                            for file_id in queued.get("files", [])}
                    expired = [file_id for file_id, item in self.pending.items()
                               if file_id not in used and
                               now - (item.get("createdAt") or
                                      Path(item["path"]).stat().st_mtime) > 30 * 86400]
                    for file_id in expired:
                        Path(self.pending.pop(file_id)["path"]).unlink(missing_ok=True)
                    if expired:
                        self._save_state()
        for part in self.upload_dir.glob(".*.part"):
            try:
                if now - part.stat().st_mtime > 86400:
                    part.unlink()
            except OSError:
                pass

    def _deliver_queued(self) -> None:
        last_cleanup = 0.0
        while not self.stop_worker.wait(2):
            if time.monotonic() - last_cleanup > 3600:
                try:
                    self.cleanup_uploads()
                except OSError as exc:
                    print(f"upload cleanup failed: {exc}", flush=True)
                last_cleanup = time.monotonic()
            with self.queue_lock:
                pending = list(self.queued_sends.items())
            checked_native = set()
            for message_id, message in pending:
                try:
                    if message.get("nativeId") or message.get("nativePending"):
                        if message["threadId"] in checked_native: continue
                        checked_native.add(message["threadId"])
                        with self.rpc_session() as rpc:
                            self.reconcile_native_queue(message['threadId'], rpc)
                        continue
                    self.send(message["text"], message["files"], message_id,
                              message.get("threadId") or self.thread_id,
                              message.get("model"), message.get("effort"))
                except BusyError:
                    continue
                except Exception as exc:
                    print(f"queued message {message_id[:8]} failed: {exc}", flush=True)
                    with self.state_lock:
                        self.last_queue_error = str(exc)[:200]
                        self._save_state()
                    continue
                with self.queue_lock:
                    with self.state_lock:
                        self.queued_sends.pop(message_id, None)
                        self.last_queue_error = ""
                        self._save_state()


class BusyError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", required=True, help="ID существующей задачи Codex")
    parser.add_argument("--bind", default="0.0.0.0", help="Адрес интерфейса; по умолчанию доступен в локальной сети")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pin", help="Одноразовый PIN сопряжения, создаётся start-bridge.sh")
    parser.add_argument("--cert", type=Path, required=True, help="TLS-сертификат; клиент сверяет его SHA-256 отпечаток")
    parser.add_argument("--key", type=Path, required=True, help="Закрытый ключ TLS")
    parser.add_argument("--inbox", type=Path, default=Path(__file__).resolve().parents[1] / "inbox")
    parser.add_argument("--outbox", type=Path, default=Path(__file__).resolve().parents[1] / "outbox")
    parser.add_argument("--state", type=Path, default=Path.home() / ".config/codex-phone-companion/state.json")
    args = parser.parse_args(argv)

    bridge_pin = args.pin or f"{secrets.randbelow(100_000_000):08d}"
    if not re.fullmatch(r"\d{8}", bridge_pin):
        parser.error("--pin должен состоять из 8 цифр")
    bridge = Bridge(args.thread, args.inbox, args.outbox, bridge_pin, args.state)
    adb_devices = AdbDevices(args.state.with_name("adb-devices.json"), os.environ.get("CODEX_ADB_RELAY", ""))
    pairing_started = [time.monotonic()]

    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexPhoneBridge/0.1"

        def handle_one_request(self):
            def expire():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.header_deadline = threading.Timer(10, expire)
            self.header_deadline.daemon = True
            self.header_deadline.start()
            try:
                super().handle_one_request()
            finally:
                self.header_deadline.cancel()

        def parse_request(self):
            self.body_consumed = False
            valid = super().parse_request()
            self.header_deadline.cancel()
            return valid

        def log_message(self, fmt, *values):
            print("bridge:", fmt % values, flush=True)

        def send_json(self, status: int, payload: dict):
            # Drain small rejected POST bodies before TLS close, otherwise clients
            # can receive a TCP reset instead of the authentication error.
            if status in (401, 403) and self.command == "POST" and not getattr(self, "body_consumed", False):
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if 0 <= length <= 16384:
                        self.read_json(16384)
                except (ValueError, OSError):
                    pass
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            return bridge.paired and hmac.compare_digest(supplied, "Bearer " + bridge.token)

        def read_json(self, max_bytes: int) -> dict:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > max_bytes:
                raise ValueError("Слишком большой или отсутствующий JSON-запрос")
            def expire_body():
                try: self.connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            deadline = threading.Timer(30, expire_body)
            deadline.daemon = True
            deadline.start()
            try:
                self.body_consumed = True
                data = json.loads(self.rfile.read(length))
            finally:
                deadline.cancel()
            if not isinstance(data, dict):
                raise ValueError("Ожидался JSON-объект")
            return data

        def guest_identity(self):
            if not bridge.paired:
                raise AccessError("Владелец отключил доступ")
            bridge.guests.bind_owner(bridge.token)
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise AccessError("Требуется гостевая сессия")
            return bridge.guests.authenticate(authorization[7:])

        def do_POST(self):
            route = urlparse(self.path).path
            if route in ("/api/guest/send", "/api/guest/cancel"):
                try:
                    guest = self.guest_identity()
                    data = self.read_json(131072)
                    if route.endswith("/send"):
                        if bridge.guest_runtime is None or not bridge.guest_runtime.enabled:
                            self.send_json(503, {"error": "Гостевой исполнитель ещё не подключён", "code": "guest_execution_unavailable"})
                            return
                        if data.get("scopeId", "default") != "default":
                            bridge.guest_resources.copy_workspace(guest, data.get("scopeId"))
                        result = bridge.guest_jobs.enqueue(guest, data)
                    else:
                        result = bridge.guest_jobs.cancel(guest, data.get("operationId"))
                    self.send_json(200, result)
                except AccessError as exc:
                    self.send_json(403, {"error": str(exc)})
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception:
                    self.send_json(503, {"error": "Гостевая очередь временно недоступна"})
                return
            if route == "/api/guest/redeem":
                if not bridge.paired:
                    self.send_json(403, {"error": "Владелец отключил доступ"}); return
                bridge.guests.bind_owner(bridge.token)
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.guests.redeem(data.get("secret"), data.get("deviceId"), data.get("sessionToken")))
                except AccessError as exc:
                    self.send_json(403, {"error": str(exc)})
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception:
                    self.send_json(500, {"error": "Не удалось принять приглашение"})
                return
            if route == "/api/guest/upload":
                try:
                    guest = self.guest_identity()
                    self.send_json(200, bridge.guest_resources.own_write(guest, self.read_json(6*1024*1024)))
                except AccessError as exc:self.send_json(403, {"error": str(exc)})
                except ValueError as exc:self.send_json(400, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "Загрузка файла недоступна"})
                return
            if route == "/api/guests/qr":
                if not self.authorized():self.send_json(403, {"error": "Только владелец создаёт QR"}); return
                try:
                    data = self.read_json(4096)
                    link = data.get("link")
                    if not isinstance(link, str) or len(link) > 2048 or urlparse(link).scheme != "codexphone" or urlparse(link).netloc != "invite": raise ValueError("Неверная ссылка приглашения")
                    import qrcode, io
                    buffer = io.BytesIO()
                    qrcode.make(link).save(buffer, format="PNG")
                    self.send_json(200, {"pngBase64": base64.b64encode(buffer.getvalue()).decode()})
                except ValueError as exc:self.send_json(400, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "QR-код недоступен; отправьте ссылку"})
                return
            if route == "/api/guest/copy":
                try:
                    guest = self.guest_identity()
                    self.send_json(200, bridge.guest_resources.prepare_copy(guest, self.read_json(4096)))
                except AccessError as exc: self.send_json(403, {"error": str(exc)})
                except ValueError as exc: self.send_json(400, {"error": str(exc)})
                except Exception: self.send_json(503, {"error": "Не удалось подготовить копию"})
                return
            if route == "/api/guests/review":
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец просматривает и принимает изменения"}); return
                try:
                    data = self.read_json(4096)
                    with bridge.send_lock:
                        if bridge.guest_runtime and bridge.guest_runtime.busy.is_set():raise ValueError("Дождитесь завершения гостевой задачи")
                        self.send_json(200, bridge.guest_resources.review(data.get("scopeId"), data.get("path"), data.get("confirmed") is True, data.get("guestHash")))
                except ValueError as exc:self.send_json(400, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "Просмотр изменений недоступен"})
                return
            if route == "/api/guests/resolve":
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец согласовывает расход"}); return
                try:
                    if bridge.guest_runtime and bridge.guest_runtime.busy.is_set():
                        raise ValueError("Дождитесь остановки исполнителя")
                    self.send_json(200, bridge.guest_jobs.resolve(self.read_json(4096)))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                return
            if route == "/api/guests/runtime":
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец управляет исполнителем"}); return
                try:
                    data = self.read_json(4096)
                    if bridge.guest_runtime is None:
                        raise ValueError("Гостевое выполнение доступно только на Linux")
                    self.send_json(200, bridge.guest_runtime.configure(data.get("enabled")))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception:
                    self.send_json(503, {"error": "Проверка гостевой изоляции не прошла"})
                return
            if route in ("/api/guests/invite", "/api/guests/action"):
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец управляет гостями"}); return
                try:
                    data = self.read_json(16384)
                    if bridge.guest_runtime and bridge.guest_runtime.enabled:bridge.guest_runtime.account_auth()
                    quotas = data.get("quotas") or {}
                    needs_sample = any(isinstance(q, dict) and q.get("mode") != "unlimited" for q in quotas.values())
                    cached_snapshot = []
                    def snapshot():
                        if not cached_snapshot:
                            cached_snapshot.append(bridge.guest_usage_limits() if needs_sample else {})
                        return cached_snapshot[0]
                    if route.endswith("/invite"):
                        result = bridge.guests.invite(data, snapshot)
                    else:
                        if data.get("action") == "grant":
                            if data.get("confirmed") is not True: raise ValueError("Подтвердите передачу содержимого выбранного ресурса")
                            bridge.guest_resources.root(data.get("kind"), data.get("resourceId"))
                        result = bridge.guests.change(data, snapshot)
                        if data.get("action") == "unshare" or (data.get("action") == "grant" and data.get("right") == "view"):
                            copies = bridge.guest_resources.shared(data.get("guestId"))["copies"]
                            for copy in copies:
                                if copy["kind"] == data.get("kind") and copy["resourceId"] == data.get("resourceId"):
                                    bridge.guest_jobs.cancel_scope(data.get("guestId"), copy["id"])
                        if data.get("action") == "revoke":
                            bridge.guest_jobs.revoke(data.get("guestId"))
                    self.send_json(200, result)
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception:
                    self.send_json(503, {"error": "Гостевой доступ временно недоступен"})
                return
            if route == "/api/update/prepare":
                if self.client_address[0] != "127.0.0.1" or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + bridge.token):
                    self.send_json(403, {"error": "Только локальный агент"}); return
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.update_status(bool(data.get("arm")), bool(data.get("release"))))
                except Exception:
                    self.send_json(409, {"error": "Не удалось проверить завершение задач"})
                return
            if route == "/api/pairing/rotate":
                if self.client_address[0] not in ("127.0.0.1", "::1") or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + bridge.token
                ):
                    self.send_json(403, {"error": "Только локальный агент может обновить код"})
                    return
                with bridge.pin_lock:
                    if bridge.paired:
                        self.send_json(409, {"error": "Телефон уже привязан"})
                        return
                    bridge.pin = f"{secrets.randbelow(100_000_000):08d}"
                    bridge.pin_uses = 0
                    pairing_started[0] = time.monotonic()
                    new_pin = bridge.pin
                self.send_json(200, {"pin": new_pin})
                return
            if route == "/api/pair":
                try:
                    client_ip = ipaddress.ip_address(self.client_address[0])
                    local_client = client_ip.is_private or client_ip in ipaddress.ip_network("100.64.0.0/10")
                except ValueError:
                    local_client = False
                if not local_client:
                    self.send_json(403, {"error": "Сопряжение разрешено только из локальной сети"})
                    return
                with bridge.pin_lock:
                    if bridge.paired:
                        self.send_json(409, {"error": "Код сопряжения уже использован; перезапусти мост для нового телефона"})
                        return
                    bridge.pin_uses += 1
                    if bridge.pin_uses > 10 or time.monotonic() - pairing_started[0] > 1800:
                        self.send_json(429, {"error": "Лимит попыток сопряжения исчерпан"})
                        return
                try:
                    data = self.read_json(4096)
                except Exception:
                    self.send_json(400, {"error": "Некорректный JSON"}); return
                with bridge.pin_lock:
                    if bridge.paired:
                        self.send_json(409, {"error": "Код сопряжения уже использован"}); return
                    if time.monotonic() - pairing_started[0] > 1800:
                        self.send_json(429, {"error": "Код сопряжения истёк"}); return
                    if not hmac.compare_digest(str(data.get("pin", "")), bridge.pin):
                        self.send_json(401, {"error": "Неверный код сопряжения"}); return
                    with bridge.state_lock:
                        bridge.paired = True
                        bridge._save_state()
                self.send_json(200, {"token": bridge.token, "threadId": bridge.selected_thread_id})
                return
            if not self.authorized():
                self.send_json(401, {"error": "Требуется сопряжение"}); return
            if route in ("/api/requests/respond", "/api/turn/interrupt"):
                try:
                    data = self.read_json(256000)
                    action = bridge.respond_request if route == "/api/requests/respond" else bridge.interrupt_turn
                    self.send_json(200, action(data))
                except (ValueError, RpcError) as exc:
                    self.send_json(409, {"error": str(exc)})
                except Exception as exc:
                    self.send_json(503, {"error": str(exc)})
                return
            if route == "/api/subagents/action":
                try:
                    self.send_json(200, bridge.subagent_action(self.read_json(128000)))
                except (ValueError, RpcError, BusyError) as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception as exc:
                    self.send_json(503, {"error": str(exc)})
                return
            if route.startswith("/api/adb/"):
                try:
                    data = self.read_json(4096)
                    if route == "/api/adb/devices":
                        result = adb_devices.add(data)
                    elif route == "/api/adb/port":
                        result = adb_devices.update_port(str(data.get("id", "")), data.get("connectPort"))
                    elif route == "/api/adb/pair":
                        result = adb_devices.pair(str(data.get("id", "")), data.get("pairingPort"), str(data.get("code", "")))
                    elif route == "/api/adb/connect":
                        result = adb_devices.connect(str(data.get("id", "")))
                    elif route == "/api/adb/delete":
                        result = adb_devices.delete(str(data.get("id", "")))
                    else:
                        self.send_json(404, {"error": "Не найдено"}); return
                    self.send_json(200, result)
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/control/action":
                try:
                    data = self.read_json(128 * 1024)
                    self.send_json(200, bridge.controls.action(data.get("threadId"), data.get("action", ""), data))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (RpcError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/unpair":
                with bridge.queue_lock:
                    with bridge.state_lock:
                        bridge.token = secrets.token_urlsafe(32)
                        bridge.paired = False
                        bridge.queued_sends.clear()
                        bridge.cancelled_sends.clear()
                        bridge.last_queue_error = ""
                        for item in bridge.pending.values():
                            try:
                                Path(item["path"]).unlink(missing_ok=True)
                            except OSError:
                                pass
                        bridge.pending.clear()
                        bridge._save_state()
                bridge.guests.bind_owner(bridge.token)
                self.send_json(200, {"revoked": True})
                return
            if route == "/api/messages/cancel":
                try:
                    data = self.read_json(4096)
                    message_id = data.get("clientMessageId")
                    if not isinstance(message_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", message_id):
                        raise ValueError("Некорректный ID сообщения")
                    self.send_json(200, bridge.dismiss_unknown_message(message_id, data.get("confirmed") is True)
                                   if data.get("dismissUnknown") else bridge.cancel_message(message_id))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (RpcError, RuntimeError, queue.Empty, OSError):
                    self.send_json(409, {"error": "Отмена не подтверждена. Обновите очередь и повторите проверку.", "code": "cancel_outcome_unknown"})
                return
            if route == "/api/limits/reset":
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.reset_limits(data.get("idempotencyKey")))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (RpcError, RuntimeError, queue.Empty) as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/messages/steer":
                try:
                    message_id = self.read_json(4096).get("clientMessageId")
                    if not isinstance(message_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", message_id):
                        raise ValueError("Некорректный ID сообщения")
                    self.send_json(200, bridge.steer_queued(message_id))
                except (RpcError, ValueError) as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/upload/cancel":
                try:
                    file_id = self.read_json(4096).get("id")
                    if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{32}", file_id):
                        raise ValueError("Некорректный ID вложения")
                    self.send_json(200, bridge.cancel_upload(file_id))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                return
            if route == "/api/threads/select":
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.select_thread(data.get("threadId", "")))
                except (RpcError, ValueError) as exc:
                    self.send_json(400, {"error": str(exc)})
                return
            if route == "/api/threads/delete":
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.delete_thread(data.get("threadId")))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (RpcError, RuntimeError, queue.Empty) as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/threads/project":
                try:
                    data = self.read_json(4096)
                    self.send_json(200, bridge.assign_thread_project(
                        data.get("threadId"), data.get("projectId")))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (RpcError, RuntimeError, queue.Empty) as exc:
                    self.send_json(409, {"error": str(exc)})
                return
            if route == "/api/threads":
                try:
                    data = self.read_json(4096)
                    cwd = data.get("cwd")
                    if cwd is not None and not isinstance(cwd, str):
                        raise ValueError("Некорректная папка проекта")
                    self.send_json(201, bridge.create_thread(cwd))
                except (RpcError, ValueError) as exc:
                    self.send_json(400, {"error": str(exc)})
                return
            if route == "/api/upload":
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    encoded_name = self.headers.get("X-Filename-Base64", "")
                    try:
                        name = base64.urlsafe_b64decode(encoded_name + "=" * (-len(encoded_name) % 4)).decode("utf-8")
                    except Exception:
                        name = "file"
                    content_type = self.headers.get("Content-Type", "application/octet-stream")
                    item = bridge.upload(name, content_type, self.rfile, length,
                                         self.headers.get("X-Content-SHA256", ""),
                                         self.headers.get("X-Upload-ID", ""))
                    self.send_json(201, item)
                except ValueError as exc:
                    self.send_json(413, {"error": str(exc)})
                except Exception as exc:
                    self.send_json(500, {"error": str(exc)})
                return
            if route == "/api/send":
                try:
                    data = self.read_json(1024 * 1024)
                    message = data.get("text", "")
                    file_ids = data.get("files", [])
                    message_id = data.get("clientMessageId") or uuid.uuid4().hex
                    thread_id = data.get("threadId") or bridge.selected_thread_id
                    model = data.get("model")
                    effort = data.get("effort")
                    if not isinstance(message, str) or len(message) > 100_000:
                        raise ValueError("Слишком длинное или некорректное сообщение")
                    if not isinstance(file_ids, list) or len(file_ids) > 20 or any(
                        not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{32}", item) for item in file_ids
                    ):
                        raise ValueError("Некорректный список вложений")
                    if not isinstance(message_id, str) or len(message_id) > 64:
                        raise ValueError("Некорректный ID сообщения")
                    if not isinstance(thread_id, str) or not VALID_THREAD_ID.fullmatch(thread_id):
                        raise ValueError("Некорректный ID чата")
                    if model is not None and (not isinstance(model, str) or len(model) > 80):
                        raise ValueError("Некорректная модель")
                    if effort is not None and (not isinstance(effort, str) or len(effort) > 24):
                        raise ValueError("Некорректное усилие")
                    result = bridge.submit(
                        message,
                        file_ids,
                        message_id,
                        thread_id,
                        model,
                        effort,
                    )
                    self.send_json(202, result)
                except (RpcError, ValueError) as exc:
                    self.send_json(400, {"error": str(exc)})
                except Exception as exc:
                    self.send_json(500, {"error": str(exc)})
                return
            self.send_json(404, {"error": "Не найдено"})

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/api/guest/workspace":
                try:
                    guest = self.guest_identity()
                    query = parse_qs(urlparse(self.path).query)
                    self.send_json(200, bridge.guest_resources.own_read(guest, query.get("scopeId", ["default"])[0], query.get("path", [""])[0], query.get("blob", [""])[0] == "true"))
                except AccessError as exc:self.send_json(403, {"error": str(exc)})
                except ValueError as exc:self.send_json(400, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "Гостевые файлы недоступны"})
                return
            if route == "/api/guest/models":
                try:
                    self.guest_identity()
                    self.send_json(200, bridge.models())
                except AccessError as exc:self.send_json(403, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "Список моделей недоступен"})
                return
            if route in ("/api/guest/shared", "/api/guest/resource", "/api/guest/history"):
                try:
                    guest = self.guest_identity()
                    query = parse_qs(urlparse(self.path).query)
                    if route.endswith("/shared"):result = bridge.guest_resources.shared(guest)
                    elif route.endswith("/history"):result = bridge.guest_resources.history(guest, query.get("threadId", [""])[0], query.get("before", [None])[0])
                    else:result = bridge.guest_resources.read(guest, query.get("kind", [""])[0], query.get("resourceId", [""])[0], query.get("path", [""])[0], query.get("preview", [""])[0] == "true",query.get("blob", [""])[0] == "true")
                    self.send_json(200, result)
                except AccessError as exc:self.send_json(403, {"error": str(exc)})
                except ValueError as exc:self.send_json(400, {"error": str(exc)})
                except Exception:self.send_json(503, {"error": "Ресурс временно недоступен"})
                return
            if route == "/api/guests/tasks":
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец просматривает общую очередь"}); return
                self.send_json(200, bridge.guest_jobs.owner_list()); return
            if route == "/api/guest/tasks":
                try:
                    guest = self.guest_identity()
                    self.send_json(200, bridge.guest_jobs.list(guest,parse_qs(urlparse(self.path).query).get("before",[None])[0]))
                except AccessError as exc:
                    self.send_json(401, {"error": str(exc)})
                except Exception:
                    self.send_json(503, {"error": "Гостевая очередь временно недоступна"})
                return
            if route == "/api/guest/self":
                if not bridge.paired:
                    self.send_json(401, {"error": "Владелец отключил доступ"}); return
                bridge.guests.bind_owner(bridge.token)
                try:
                    authorization = self.headers.get("Authorization", "")
                    if not authorization.startswith("Bearer "):
                        raise AccessError("Требуется гостевая сессия")
                    guest = bridge.guests.authenticate(authorization[7:])
                    result = bridge.guest_display(guest)
                    result["executionAvailable"] = bool(bridge.guest_runtime and bridge.guest_runtime.enabled)
                    result["runtime"] = bridge.guest_runtime.status() if bridge.guest_runtime else {"enabled": False, "supported": False}
                    self.send_json(200, result)
                except AccessError as exc:
                    self.send_json(401, {"error": str(exc)})
                return
            if route == "/api/guests":
                if not self.authorized():
                    self.send_json(403, {"error": "Только владелец управляет гостями"}); return
                result = bridge.guests.list()
                guests=[]
                for item in result["guests"]:
                    try:guests.append({**item,**bridge.guest_display(item["id"])})
                    except AccessError:continue
                result["guests"]=guests
                result["executionAvailable"] = bool(bridge.guest_runtime and bridge.guest_runtime.enabled)
                result["runtime"] = bridge.guest_runtime.status() if bridge.guest_runtime else {"enabled": False, "supported": False}
                self.send_json(200, result)
                return
            if urlparse(self.path).path == "/api/health":
                if self.client_address[0] not in ("127.0.0.1", "::1") or not hmac.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + bridge.token):
                    self.send_json(403, {"error": "Только локальный агент"}); return
                try:
                    probe = parse_qs(urlparse(self.path).query).get("probe", [""])[0] == "true"
                    self.send_json(200, bridge.health(probe))
                except Exception:
                    self.send_json(503, {"bridgeReady": True, "codexReady": False, "codexState": "failed"})
                return
            if not self.authorized():
                self.send_json(401, {"error": "Требуется сопряжение"}); return
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                thread_id = parse_qs(parsed.query).get("threadId", [None])[0]
                if route == "/api/capabilities": self.send_json(200, bridge.capabilities())
                elif route == "/api/status": self.send_json(200, bridge.status(thread_id))
                elif route == "/api/history":
                    query = parse_qs(parsed.query)
                    self.send_json(200, bridge.history(thread_id,
                        query.get("before", [None])[0], query.get("limit", [60])[0]))
                elif route == "/api/events":
                    query = parse_qs(parsed.query)
                    self.send_json(200, bridge.wait_event(query.get("cursor", [""])[0]))
                elif route == "/api/subagents":
                    self.send_json(200, bridge.subagents(thread_id))
                elif route == "/api/subagents/history":
                    query = parse_qs(parsed.query)
                    agent_id = query.get("agentId", [""])[0]
                    bridge.subagent_check(thread_id, agent_id)
                    self.send_json(200, bridge.history(agent_id, query.get("before", [None])[0]))
                elif route == "/api/control":
                    q = parse_qs(parsed.query)
                    self.send_json(200, bridge.controls.read(thread_id, q.get("section", [""])[0],
                        {k:v[0] for k,v in q.items()}))
                elif route == "/api/requests": self.send_json(200, bridge.requests(thread_id))
                elif route == "/api/task": self.send_json(200, bridge.task_result(thread_id, parse_qs(parsed.query).get("turnId", [""])[0]))
                elif route == "/api/changes": self.send_json(200, bridge.changes(thread_id, parse_qs(parsed.query).get("turnId", [None])[0]))
                elif route == "/api/projects": self.send_json(200, bridge.projects())
                elif route == "/api/threads": self.send_json(200, {"threads": bridge.list_threads(limit=500),
                                                                    "selectedThreadId": bridge.selected_thread_id})
                elif route == "/api/models": self.send_json(200, bridge.models())
                elif route == "/api/limits": self.send_json(200, bridge.usage_limits())
                elif route == "/api/adb/devices": self.send_json(200, adb_devices.list())
                elif route == "/api/outbox": self.send_json(200, {"files": bridge.outbox()})
                elif route == "/api/workspace":
                    relative = parse_qs(parsed.query).get("path", [""])[0]
                    q = parse_qs(parsed.query)
                    self.send_json(200, bridge.workspace_list(thread_id, relative, q.get("query", [""])[0],
                        q.get("hidden", ["false"])[0] == "true", q.get("service", ["false"])[0] == "true", q.get("cursor", [None])[0]))
                elif route == "/api/workspace/resolve":
                    reference = parse_qs(parsed.query).get("path", [""])[0]
                    self.send_json(200, bridge.workspace_resolve(thread_id, reference))
                elif route == "/api/workspace/preview":
                    relative = parse_qs(parsed.query).get("path", [""])[0]
                    self.send_json(200, bridge.workspace_preview(thread_id, relative))
                elif route in ("/api/chat/image", "/api/workspace/image", "/api/outbox/image"):
                    query = parse_qs(parsed.query)
                    if route == "/api/chat/image":
                        path = bridge.chat_image(query.get("id", [""])[0])
                    elif route == "/api/outbox/image":
                        path = bridge.outbox_file(query.get("id", [""])[0])
                    else:
                        _, path = bridge.workspace_path(thread_id, query.get("path", [""])[0])
                    if path is None or not path.is_file() or path.suffix.lower() not in IMAGE_MIME:
                        self.send_json(404, {"error": "Изображение не найдено"}); return
                    if path.stat().st_size > MAX_IMAGE_BYTES:
                        self.send_json(413, {"error": "Изображение слишком большое"}); return
                    data = path.read_bytes()
                    mime = IMAGE_MIME[path.suffix.lower()]
                    if query.get("size", [""])[0] == "thumb" and Image is not None:
                        with Image.open(BytesIO(data)) as source:
                            source = ImageOps.exif_transpose(source)
                            source.thumbnail((160, 160))
                            thumb = BytesIO()
                            source.convert("RGB").save(thumb, format="JPEG", quality=78)
                            data, mime = thumb.getvalue(), "image/jpeg"
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "private, max-age=60")
                    self.end_headers()
                    self.wfile.write(data)
                elif route in ("/api/download", "/api/workspace/download"):
                    query = parse_qs(parsed.query)
                    if route == "/api/download":
                        path = bridge.outbox_file(query.get("id", [""])[0])
                    else:
                        _, path = bridge.workspace_path(thread_id, query.get("path", [""])[0])
                        if not path.is_file():
                            self.send_json(404, {"error": "Файл не найден"})
                            return
                        if path.stat().st_size > MAX_UPLOAD_BYTES:
                            self.send_json(413, {"error": "Файл слишком большой для скачивания"})
                            return
                    if path is None:
                        self.send_json(404, {"error": "Файл не найден"})
                        return
                    size = path.stat().st_size
                    digest = hashlib.sha256()
                    with path.open("rb") as source:
                        while chunk := source.read(64 * 1024):
                            digest.update(chunk)
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("X-Content-SHA256", digest.hexdigest())
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with path.open("rb") as source:
                        while chunk := source.read(64 * 1024):
                            self.wfile.write(chunk)
                else: self.send_json(404, {"error": "Не найдено"})
            except RpcError as exc:
                self.send_json(409 if "active writer" in str(exc).lower() else 500, {"error": str(exc)})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 32
        workers = threading.BoundedSemaphore(32)

        def process_request(self, request, client_address):
            if not self.workers.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.workers.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.workers.release()

        def finish_request(self, request, client_address):
            # The socket must be accepted before a TLS handshake can block.
            # ThreadingHTTPServer runs finish_request in a worker thread.
            request.settimeout(10)
            try:
                with self.tls.wrap_socket(request, server_side=True) as secured:
                    secured.settimeout(30)
                    Handler(secured, client_address, self)
            except (ssl.SSLError, TimeoutError, ConnectionResetError):
                # A readiness probe or abandoned connection may not send TLS.
                pass

    server = Server((args.bind, args.port), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(str(args.cert), str(args.key))
    server.tls = tls
    print(f"Codex Phone Bridge слушает {args.bind}:{args.port}", flush=True)
    print(f"Задача: {args.thread}", flush=True)
    print("Код первичной привязки хранится отдельно и действует 30 минут.", flush=True)
    print(f"Загрузки: {bridge.upload_dir}", flush=True)
    print("Токен выдаётся только после сопряжения и не печатается в журнал.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        bridge.stop_worker.set()
        if bridge.guest_runtime: bridge.guest_runtime.close()
        with bridge.rpc_lock:
            if bridge.rpc is not None:
                bridge.rpc.close()


if __name__ == "__main__":
    main()
