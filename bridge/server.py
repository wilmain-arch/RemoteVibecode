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
import subprocess
import threading
import time
import uuid
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote
try:
    from .adb_devices import AdbDevices
except ImportError:
    from adb_devices import AdbDevices

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
    def __init__(self, on_event=None) -> None:
        try:
            from .codex_path import resolve_codex_executable
        except ImportError:
            from codex_path import resolve_codex_executable
        executable = resolve_codex_executable(os.environ.get("CODEX_EXECUTABLE"))
        if not executable:
            raise RuntimeError("Не найден исполняемый файл Codex Desktop или CLI")
        app_server_args = ["app-server", "--listen", "stdio://"] if sys.platform == "win32" else ["app-server", "--stdio"]
        self.proc = subprocess.Popen(
            [executable, *app_server_args],
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
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        result = self.call(
            "initialize",
            {
                "clientInfo": {"name": "codex-phone-companion", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        if "error" in result:
            raise RuntimeError(result["error"].get("message", "Codex initialization failed"))
        self.notify("initialized", {})

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = data.get("id")
            if isinstance(request_id, int):
                with self._waiters_lock:
                    waiter = self._waiters.get(request_id)
                if waiter is not None:
                    waiter.put(data)
            elif data.get("method"):
                if self.on_event:
                    self.on_event(data)
                try:
                    self.events.put_nowait(data)
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
            response = waiter.get(timeout=timeout)
            if "error" in response:
                error = response["error"]
                raise RpcError(error.get("message", "Codex request failed"), error.get("code"))
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
        self.pin = pin
        self.pin_uses = 0
        self.pin_lock = threading.Lock()
        saved = self._load_state()
        self.token = saved.get("token") or secrets.token_urlsafe(32)
        self.paired = bool(saved.get("paired", False))
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
        self.active_turns: dict[str, str] = {}
        self.turn_lock = threading.Lock()
        self.event_condition = threading.Condition()
        self.event_instance = secrets.token_hex(8)
        self.event_count = 0
        self.send_lock = threading.Lock()
        self.update_until = 0.0
        self.upload_lock = threading.Lock()
        self.pending: dict[str, dict] = {
            key: item for key, item in saved.get("pending", {}).items()
            if isinstance(item, dict) and Path(item.get("path", "")).is_file()
            and Path(item["path"]).resolve().parent == self.upload_dir
        }
        self.sent_messages: dict[str, dict] = saved.get("sent_messages", {})
        self.queued_sends: dict[str, dict] = saved.get("queued_sends", {})
        self.cancelled_sends: dict[str, float] = saved.get("cancelled_sends", {})
        self.last_queue_error = saved.get("last_queue_error", "")
        self.queue_lock = threading.Lock()
        self.chat_files: dict[tuple[str, str], Path] = {}
        self.chat_images: dict[str, Path] = {}
        self.chat_images_lock = threading.Lock()
        self.agent_labels = {}
        self.agent_labels_lock = threading.Lock()
        self._save_state()
        self.stop_worker = threading.Event()
        self.worker = threading.Thread(target=self._deliver_queued, daemon=True)
        self.worker.start()
        self.rpc_idle_worker = threading.Thread(target=self._close_idle_rpc, daemon=True)
        self.rpc_idle_worker.start()
        threading.Thread(target=self._track_task_usage, daemon=True).start()

    @contextmanager
    def rpc_session(self):
        with self.rpc_lock:
            if self.rpc is None or self.rpc.proc.poll() is not None:
                with self.turn_lock:
                    self.active_turns.clear()
                self.rpc = CodexRpc(self._signal_event)
            rpc = self.rpc
            try:
                yield rpc
            finally:
                self.rpc_last_used = time.monotonic()

    def _signal_event(self, event: dict | None = None) -> None:
        if event:
            method = event.get("method", "")
            params = event.get("params") or {}
            thread_id = params.get("threadId")
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
        with self.queue_lock:
            if thread_id in self.active_turns or any(
                    item.get("threadId") == thread_id for item in self.queued_sends.values()):
                raise RuntimeError("Дождитесь завершения задачи и сообщений в очереди")
            if thread_id in self.draft_threads:
                with self.state_lock:
                    self.draft_threads.pop(thread_id, None)
            else:
                if not any(item["id"] == thread_id for item in self.list_threads(limit=500)):
                    raise ValueError("Чат не найден")
                with self.rpc_session() as rpc:
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
        if cwd:
            path = Path(cwd).resolve()
            if not path.is_dir():
                raise ValueError("Рабочая папка проекта не найдена")
            # A new chat may use only a working directory already visible in this account.
            known = {item.get("cwd") for item in self.list_threads(limit=500)}
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
        if any(part in (".", "..", "__pycache__", "node_modules", "build")
               or part.startswith(".") for part in parts):
            raise ValueError("Этот путь недоступен")
        path = root
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("Ссылки на файлы недоступны")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Путь выходит за пределы проекта")
        return root, path

    def workspace_list(self, thread_id: str | None, relative: str = "") -> dict:
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
        entries = []
        for path in directory.iterdir():
            if path.name.startswith(".") or path.name in {"__pycache__", "node_modules", "build"} or path.is_symlink():
                continue
            try:
                is_dir = path.is_dir()
                if not is_dir and not path.is_file():
                    continue
                entries.append({"name": path.name, "path": str(path.relative_to(root)),
                                "isDirectory": is_dir, "size": 0 if is_dir else path.stat().st_size})
            except OSError:
                continue
        entries.sort(key=lambda item: (not item["isDirectory"], item["name"].casefold()))
        return {"rootName": root.name, "path": relative, "entries": entries[:200],
                "truncated": len(entries) > 200}

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
        root, _ = self.workspace_path(thread_id)
        candidate = Path(reference)
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
                "isDirectory": path.is_dir()}

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
                "pending": self.pending,
                "sent_messages": self.sent_messages,
                "queued_sends": self.queued_sends,
                "cancelled_sends": self.cancelled_sends,
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
                "thread/read", {"threadId": thread_id, "includeTurns": False}
            )
        thread = result.get("thread", {})
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
                limit: int = 60) -> dict:
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
                                      "text": text, "attachments": attachments[:20], "images": images[:20],
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
        start = max(0, end - limit)
        page = turns[start:end]
        return {"threadId": thread_id, "turns": page, "hasMore": start > 0,
                "nextBefore": page[0]["id"] if page and start > 0 else None}

    def upload(self, original_name: str, content_type: str, stream, length: int,
               expected_sha256: str = "", client_upload_id: str = "") -> dict:
        if client_upload_id and not re.fullmatch(r"[0-9a-f]{32}", client_upload_id):
            raise ValueError("Некорректный ID загрузки")
        with self.upload_lock:
            if client_upload_id:
                with self.state_lock:
                    existing = self.pending.get(client_upload_id)
                    if existing is not None:
                        if (existing.get("size") != length or
                            expected_sha256 and existing.get("sha256") != expected_sha256.lower()):
                            raise ValueError("ID загрузки уже занят другим файлом")
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

    def send(self, text: str, file_ids: list[str], client_message_id: str,
             thread_id: str | None = None, model: str | None = None,
             effort: str | None = None) -> dict:
        thread_id = self._thread_id(thread_id)
        with self.send_lock:
            if time.monotonic() < self.update_until:
                raise ValueError("Агент устанавливает обновление. Повторите отправку после подключения.")
            if client_message_id in self.cancelled_sends:
                raise ValueError("Сообщение отменено")
            if client_message_id in self.sent_messages:
                return self.sent_messages[client_message_id]
            with self.turn_lock:
                if thread_id in self.active_turns:
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
                draft_id = thread_id if thread_id in self.draft_threads else None
                if draft_id:
                    cwd = self.draft_threads[draft_id]
                    start_params = {"serviceName": "codex_phone_companion"}
                    if cwd:
                        start_params["cwd"] = cwd
                    draft_project = self.project_overrides.get(draft_id)
                    if draft_project:
                        start_params["projectId"] = draft_project
                    created = rpc.call("thread/start", start_params)
                    thread_id = created.get("thread", {}).get("id")
                    if not thread_id:
                        raise RuntimeError("Codex не создал чат")
                else:
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
                try:
                    try:
                        usage_before = self.usage_limits()
                    except (RpcError, RuntimeError, OSError, queue.Empty):
                        usage_before = None
                    result = rpc.call("turn/start", params)
                except RpcError as exc:
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
            with self.state_lock:
                if draft_id:
                    self.draft_threads.pop(draft_id, None)
                    if draft_id in self.project_overrides:
                        self.project_overrides[thread_id] = self.project_overrides.pop(draft_id)
                    if self.selected_thread_id == draft_id:
                        self.selected_thread_id = thread_id
                for file_id in file_ids:
                    self.pending.pop(file_id, None)
                self.sent_messages[client_message_id] = response
                if len(self.sent_messages) > 512:
                    self.sent_messages.pop(next(iter(self.sent_messages)))
                self._save_state()
            return response

    def submit(self, text: str, file_ids: list[str], client_message_id: str,
               thread_id: str | None = None, model: str | None = None,
               effort: str | None = None) -> dict:
        thread_id = self._thread_id(thread_id)
        if client_message_id in self.sent_messages:
            return self.sent_messages[client_message_id]
        if client_message_id in self.cancelled_sends:
            raise ValueError("Сообщение отменено")
        with self.queue_lock:
            if client_message_id in self.queued_sends:
                return {"queued": True, "threadId": thread_id, "clientMessageId": client_message_id}
        try:
            return self.send(text, file_ids, client_message_id, thread_id, model, effort)
        except BusyError:
            with self.queue_lock:
                with self.state_lock:
                    if client_message_id in self.cancelled_sends:
                        raise ValueError("Сообщение отменено")
                    if len(self.queued_sends) >= 50:
                        raise ValueError("Очередь заполнена; дождись доставки предыдущих сообщений")
                    self.queued_sends[client_message_id] = {
                        "text": text,
                        "files": file_ids,
                        "clientMessageId": client_message_id,
                        "threadId": thread_id,
                        "model": model,
                        "effort": effort,
                        "queuedAt": time.time(),
                    }
                    self.last_queue_error = ""
                    self._save_state()
            return {"queued": True, "threadId": thread_id, "clientMessageId": client_message_id}

    def cancel_message(self, client_message_id: str) -> dict:
        with self.send_lock:
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
            for message_id, message in pending:
                try:
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

        def log_message(self, fmt, *values):
            print("bridge:", fmt % values, flush=True)

        def send_json(self, status: int, payload: dict):
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
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Ожидался JSON-объект")
            return data

        def do_POST(self):
            route = urlparse(self.path).path
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
                if not hmac.compare_digest(str(data.get("pin", "")), bridge.pin):
                    self.send_json(401, {"error": "Неверный код сопряжения"}); return
                with bridge.state_lock:
                    bridge.paired = True
                    bridge._save_state()
                self.send_json(200, {"token": bridge.token, "threadId": bridge.selected_thread_id})
                return
            if not self.authorized():
                self.send_json(401, {"error": "Требуется сопряжение"}); return
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
                self.send_json(200, {"revoked": True})
                return
            if route == "/api/messages/cancel":
                try:
                    message_id = self.read_json(4096).get("clientMessageId")
                    if not isinstance(message_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", message_id):
                        raise ValueError("Некорректный ID сообщения")
                    self.send_json(200, bridge.cancel_message(message_id))
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
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
            if not self.authorized():
                self.send_json(401, {"error": "Требуется сопряжение"}); return
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                thread_id = parse_qs(parsed.query).get("threadId", [None])[0]
                if route == "/api/status": self.send_json(200, bridge.status(thread_id))
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
                elif route == "/api/projects": self.send_json(200, bridge.projects())
                elif route == "/api/threads": self.send_json(200, {"threads": bridge.list_threads(limit=500),
                                                                    "selectedThreadId": bridge.selected_thread_id})
                elif route == "/api/models": self.send_json(200, bridge.models())
                elif route == "/api/limits": self.send_json(200, bridge.usage_limits())
                elif route == "/api/adb/devices": self.send_json(200, adb_devices.list())
                elif route == "/api/outbox": self.send_json(200, {"files": bridge.outbox()})
                elif route == "/api/workspace":
                    relative = parse_qs(parsed.query).get("path", [""])[0]
                    self.send_json(200, bridge.workspace_list(thread_id, relative))
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
        request_queue_size = 128

        def finish_request(self, request, client_address):
            # The socket must be accepted before a TLS handshake can block.
            # ThreadingHTTPServer runs finish_request in a worker thread.
            request.settimeout(10)
            try:
                with self.tls.wrap_socket(request, server_side=True) as secured:
                    secured.settimeout(None)
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
        with bridge.rpc_lock:
            if bridge.rpc is not None:
                bridge.rpc.close()


if __name__ == "__main__":
    main()
