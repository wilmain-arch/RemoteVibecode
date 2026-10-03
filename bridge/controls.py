"""Bounded, authenticated second-stage controls; no arbitrary RPC or shell proxy."""
from pathlib import Path
from contextlib import nullcontext
import json
import hashlib
import os
import re
import subprocess
import threading
import tempfile

ID = re.compile(r'[a-zA-Z0-9_-]{1,100}')


def value(data, key, maximum=1000, optional=False):
    v = data.get(key, '')
    if not isinstance(v, str) or len(v) > maximum or (not optional and not v.strip()) or '\x00' in v:
        raise ValueError('Некорректное поле: ' + key)
    return v.strip()


def ident(data, key):
    v = value(data, key, 100)
    if not ID.fullmatch(v):
        raise ValueError('Некорректный идентификатор: ' + key)
    return v


class Controls:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = threading.RLock()

    def idle(self, rpc, tid):
        thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': True}).get('thread', {})
        status = thread.get('status') or {}
        if (isinstance(status, dict) and status.get('type') in ('active', 'running')) or any(
            t.get('status') in ('inProgress', 'active') for t in thread.get('turns', [])):
            raise ValueError('Дождитесь завершения активной задачи')
        with self.bridge.queue_lock:
            if any(m.get('threadId') == tid for m in self.bridge.queued_sends.values()):
                raise ValueError('Сначала очистите очередь сообщений')
        q = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 1})
        if q.get('data'):
            raise ValueError('Сначала очистите очередь Desktop')
        return thread

    def read(self, tid, section, data):
        b = self.bridge
        tid = b._thread_id(tid)
        if section == 'git':
            return self.git(tid, 'status', data)
        if tid in b.draft_threads and section not in ('archive', 'projects'):
            return {'data': [], 'note': 'Сначала отправьте первое сообщение в этом чате'}
        with b.rpc_session() as rpc:
            if section == 'archive':
                return rpc.call('thread/list', {'archived': True, 'limit': 40,
                    'cursor': data.get('cursor') or None})
            if section == 'search':
                q = value(data, 'query', 200)
                return rpc.call('thread/searchOccurrences', {'threadId': tid, 'searchTerm': q,
                    'limit': 30, 'cursor': data.get('cursor') or None})
            if section == 'plan':
                thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': True}).get('thread', {})
                plans = [dict(item, turnId=t.get('id')) for t in thread.get('turns', [])
                         for item in t.get('items', []) if item.get('type') == 'plan']
                with b.state_lock:
                    published = [dict(p, turnId=turn) for turn,p in b.published_plans.items() if p.get('threadId') == tid][-10:]
                return {'plans': plans[-10:], 'publishedPlans': published, 'goal': rpc.call('thread/goal/get', {'threadId': tid}).get('goal'),
                        'modes': rpc.call('collaborationMode/list', {}).get('data', []),
                        'selectedMode': b.thread_modes.get(tid, 'default')}
            if section == 'queue':
                result = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 50,
                    'cursor': data.get('cursor') or None})
                with b.queue_lock:
                    result['bridgeQueue'] = [dict(m, id=k) for k,m in b.queued_sends.items() if m.get('threadId') == tid and not m.get('nativeId')]
                with b.state_lock:
                    result['cancelledIds'] = list(b.cancelled_sends)[-512:]
                    result['receipts'] = {mid:r for mid,r in b.sent_messages.items() if r.get('threadId') == tid}
                return result
            if section == 'projects':
                return rpc.call('project/list', {'limit': 100, 'cursor': data.get('cursor') or None})
        raise ValueError('Неизвестный раздел')

    def action(self, tid, action, data):
        opid = data.get('operationId')
        if action not in ('fork', 'revert') or opid is None:
            return self._perform(tid, action, data)
        opid = ident(data, 'operationId')
        fingerprint = hashlib.sha256(json.dumps({'threadId': tid, 'action': action, 'data': data}, sort_keys=True).encode()).hexdigest()
        with self.bridge.send_lock, self.lock:
            prior = self.bridge.control_operations.get(opid)
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise ValueError('Идентификатор операции уже использован для другого запроса')
                return prior['result']
            result = self._perform(tid, action, data, lock_held=True)
            with self.bridge.state_lock:
                self.bridge.control_operations[opid] = {'fingerprint': fingerprint, 'result': result}
                while len(self.bridge.control_operations)>200:
                    self.bridge.control_operations.pop(next(iter(self.bridge.control_operations)))
                self.bridge._save_state()
            return result

    def _perform(self, tid, action, data, lock_held=False):
        b = self.bridge
        tid = b._thread_id(tid)
        if not isinstance(action, str):
            raise ValueError('Некорректное действие')
        guard = nullcontext() if lock_held else b.send_lock
        if action.startswith('git-'):
            with guard, self.lock, b.rpc_session() as rpc:
                self.idle(rpc, tid)
                return self.git(tid, action[4:], data)
        if action == 'folder-create':
            relative = value(data, 'path', 500)
            if relative.startswith('@chat-files/'):
                raise ValueError('Создавать папки можно только внутри рабочего проекта')
            _, path = b.workspace_path(tid, relative)
            path.mkdir(exist_ok=False)
            return {'created': True, 'path': relative}
        handlers = {
            'rename': self._rename, 'archive': self._archive, 'restore': self._restore,
            'fork': self._history_action, 'revert': self._history_action,
            'mode': self._mode, 'goal-set': self._goal_set, 'goal-status': self._goal_status,
            'goal-clear': self._goal_clear, 'project-create': self._project_create,
            'project-rename': self._project_rename, 'project-delete': self._project_delete,
            'review': self._review,
        }
        handler = self._queue_action if action in ('queue-add','queue-update','queue-delete','queue-reorder','queue-start') else handlers.get(action)
        if handler is None:
            raise ValueError('Неизвестное действие')
        with guard, self.lock, b.rpc_session() as rpc:
            return handler(rpc, tid, data, action)

    def _rename(self, rpc, tid, data, action):
        b = self.bridge
        return rpc.call('thread/name/set', {'threadId': tid, 'name': value(data, 'name', 200)})

    def _archive(self, rpc, tid, data, action):
        b = self.bridge
        archived_thread = self.idle(rpc, tid)
        rpc.call('thread/archive', {'threadId': tid})
        remaining = b.list_threads(limit=500)
        next_id = next((t['id'] for t in remaining if t['id'] != tid), None)
        if next_id is None:
            import uuid
            next_id = 'draft-' + uuid.uuid4().hex
            b.draft_threads[next_id] = archived_thread.get('cwd') or str(Path.home())
        with b.state_lock:
            b.selected_thread_id = next_id
            b._save_state()
        return {'archived': True, 'threadId': next_id}

    def _restore(self, rpc, tid, data, action):
        b = self.bridge
        return rpc.call('thread/unarchive', {'threadId': ident(data, 'targetId')})

    def _history_action(self, rpc, tid, data, action):
        b = self.bridge
        thread = self.idle(rpc, tid)
        turn = ident(data, 'turnId')
        if not any(t.get('id') == turn for t in thread.get('turns', [])):
            raise ValueError('Ход не найден в истории этого чата')
        if action == 'fork':
            result = rpc.call('thread/fork', {'threadId': tid, 'lastTurnId': turn, 'deferGoalContinuation': True})
            return {'threadId': result.get('thread', {}).get('id')}
        if data.get('confirmed') is not True:
            raise ValueError('Подтвердите удаление выбранного хода и последующей истории. Файлы не откатываются')
        rpc.call('thread/revert', {'threadId': tid, 'beforeTurnId': turn})
        return {'reverted': True}

    def _mode(self, rpc, tid, data, action):
        b = self.bridge
        mode = value(data, 'mode', 32)
        allowed = {x.get('mode') for x in rpc.call('collaborationMode/list', {}).get('data', [])}
        if mode not in allowed:
            raise ValueError('Режим не поддерживается')
        with b.state_lock:
            b.thread_modes[tid] = mode
            b._save_state()
        return {'mode': mode, 'appliesTo': 'nextTurn'}

    def _goal_set(self, rpc, tid, data, action):
        b = self.bridge
        objective = value(data, 'objective', 4000)
        # Creating a paused goal never launches an automatic continuation.
        params = {'threadId': tid, 'objective': objective, 'status': 'paused'}
        budget = data.get('tokenBudget')
        if budget not in (None, ''):
            if not isinstance(budget, str) or not budget.isdigit() or not 1 <= int(budget) <= 100000000:
                raise ValueError('Бюджет токенов должен быть от 1 до 100000000')
            params['tokenBudget'] = int(budget)
        return rpc.call('thread/goal/set', params)

    def _goal_status(self, rpc, tid, data, action):
        b = self.bridge
        status = value(data, 'status', 30)
        if status not in ('active', 'paused', 'complete'):
            raise ValueError('Состояние цели не поддерживается')
        if status == 'active' and data.get('confirmed') is not True:
            raise ValueError('Возобновление может запустить работу и расходовать лимиты; подтвердите действие')
        return rpc.call('thread/goal/set', {'threadId': tid, 'status': status})

    def _goal_clear(self, rpc, tid, data, action):
        b = self.bridge
        return rpc.call('thread/goal/clear', {'threadId': tid})

    def _queue_action(self, rpc, tid, data, action):
        b = self.bridge
        op = action[6:]
        params = {'threadId': tid}
        if op in ('add', 'start') and data.get('confirmed') is not True:
            raise ValueError('Выполнение может расходовать лимиты; требуется подтверждение')
        if op in ('update', 'delete', 'start'):
            params['queuedSubmissionId'] = ident(data, 'submissionId')
        if op == 'update':
            existing = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 100}).get('data', [])
            msg = next((m for m in existing if m.get('id') == params['queuedSubmissionId']), None)
            if msg is None or any(x.get('type') != 'text' for x in msg.get('input', [])):
                raise ValueError('Сообщение не найдено или содержит вложения; измените его на ПК')
        if op in ('add', 'update'):
            params['input'] = [{'type': 'text', 'text': value(data, 'text', 100000)}]
        if op == 'add':
            params['clientUserMessageId'] = ident(data, 'messageId')
            # A retried form may arrive after the original queue entry already started.
            thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': True}).get('thread', {})
            for turn in thread.get('turns', []):
                if any(item.get('type') == 'userMessage' and item.get('clientId') == params['clientUserMessageId'] for item in turn.get('items', [])):
                    return {'alreadyDelivered': True, 'turnId': turn.get('id')}
            queued = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 100})
            prior = next((msg for msg in queued.get('data', []) if msg.get('clientUserMessageId') == params['clientUserMessageId']), None)
            if prior:
                return {'queuedSubmission': prior}
            if queued.get('nextCursor'):
                raise ValueError('Очередь слишком велика для безопасной проверки повторной отправки')
        if op == 'reorder':
            ids = data.get('ids')
            if not isinstance(ids, list) or not ids or len(ids)>50 or any(not isinstance(x,str) or not ID.fullmatch(x) for x in ids) or len(set(ids))!=len(ids):
                raise ValueError('Некорректный порядок очереди')
            actual = rpc.call('thread/queue/list', {'threadId': tid, 'limit': 100})
            if actual.get('nextCursor') or set(ids) != {m.get('id') for m in actual.get('data', [])}:
                raise ValueError('Очередь изменилась; обновите список')
            params['queuedSubmissionIds'] = ids
        if op == 'start':
            self.idle_for_queue_start(rpc, tid)
        if op not in ('add', 'update', 'delete', 'start', 'reorder'):
            raise ValueError('Неизвестное действие очереди')
        result = rpc.call('thread/queue/' + op, params)
        if op in ('delete', 'update'):
            with b.queue_lock, b.state_lock:
                for mid, msg in list(b.queued_sends.items()):
                    if msg.get('threadId') == tid and msg.get('nativeId') == params['queuedSubmissionId']:
                        if op == 'delete':
                            import time
                            b.queued_sends.pop(mid, None)
                            b.cancelled_sends[mid] = time.time()
                        else:
                            msg['text'] = params['input'][0]['text']
                b._save_state()
        return result

    def _project_create(self, rpc, tid, data, action):
        b = self.bridge
        path = Path(value(data, 'path', 2000)).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('Укажите абсолютный путь к существующей папке')
        return rpc.call('project/create', {'name': value(data, 'name', 200), 'roots': [{'path': str(path.resolve())}],
            'idempotencyKey': ident(data, 'messageId')})

    def _project_rename(self, rpc, tid, data, action):
        b = self.bridge
        return rpc.call('project/update', {'projectId': value(data, 'projectId', 200), 'name': value(data, 'name', 200)})

    def _project_delete(self, rpc, tid, data, action):
        b = self.bridge
        if data.get('confirmed') is not True:
            raise ValueError('Подтвердите удаление проекта из списка; папка и файлы остаются')
        return rpc.call('project/delete', {'projectId': value(data, 'projectId', 200)})

    def _review(self, rpc, tid, data, action):
        b = self.bridge
        self.idle(rpc, tid)
        if data.get('confirmed') is not True:
            raise ValueError('Ревью расходует лимит; требуется подтверждение')
        return rpc.call('review/start', {'threadId': tid, 'target': {'type': 'uncommittedChanges'}, 'delivery': 'inline'})


    def idle_for_queue_start(self, rpc, tid):
        thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': True}).get('thread', {})
        if any(t.get('status') in ('inProgress', 'active') for t in thread.get('turns', [])):
            raise ValueError('Сначала завершите или остановите активную задачу')

    def git(self, tid, action, data):
        root, _ = self.bridge.workspace_path(tid, '')
        def run(*args, limit=512000):
            # Spool output to disk: a large diff must not allocate its full size in RAM.
            with tempfile.TemporaryDirectory(prefix='rv-empty-hooks-') as hooks, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                proc = subprocess.run(['git', '-c', 'core.hooksPath='+hooks, '-C', str(root), *args], stdout=stdout,
                    stderr=stderr, timeout=30, env={**os.environ, 'GIT_TERMINAL_PROMPT':'0'})
                if proc.returncode:
                    stderr.seek(0)
                    raise ValueError(stderr.read(1000).decode(errors='replace') or 'Ошибка Git')
                stdout.seek(0)
                raw = stdout.read(limit + 1)
                return raw[:limit].decode(errors='replace'), len(raw) > limit
        top, _ = run('rev-parse', '--show-toplevel')
        # Only operate on a repository rooted in the chat workspace, never a parent repository.
        if Path(top.strip()).resolve() != root.resolve():
            raise ValueError('Корень Git отличается от рабочей папки чата')
        if action == 'status':
            status,_=run('status','--porcelain=v1',limit=100000)
            branches,_=run('for-each-ref','--format=%(refname:short)','refs/heads',limit=100000)
            current,_=run('branch','--show-current')
            try:
                run('rev-parse','--verify','HEAD')
                diff,truncated=run('diff','HEAD','--no-ext-diff','--no-textconv')
            except ValueError:
                diff,truncated=run('diff','--cached','--no-ext-diff','--no-textconv')
            worktrees,_=run('worktree','list','--porcelain')
            return {'status':status,'branches':branches.splitlines(),'branch':current.strip(),'diff':diff,'truncated':truncated,'worktrees':worktrees}
        if data.get('confirmed') is not True:
            raise ValueError('Подтвердите действие Git')
        if action in ('switch','worktree'):
            branch=value(data,'branch',200)
            if branch.startswith('-'):
                raise ValueError('Некорректная ветка')
            run('check-ref-format','--branch',branch)
            if action=='switch':
                dirty,_=run('status','--porcelain=v1')
                if dirty.strip():raise ValueError('Есть незакоммиченные изменения. Переключение отменено')
                run('switch','--',branch)
            else:
                name=value(data,'name',80)
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',name):raise ValueError('Имя worktree: латиница, цифры, дефис')
                target=root.parent/(root.name+'-'+name)
                if target.exists():raise ValueError('Папка уже существует')
                run('worktree','add','--',str(target),branch)
            return {'done':True}
        if action=='stage':
            run('add','-A','--','.')
            return {'done':True}
        if action=='commit':
            message=value(data,'message',2000)
            staged,_=run('diff','--cached','--name-only')
            if not staged.strip():raise ValueError('Нет подготовленных файлов. Добавьте файлы в индекс на ПК')
            run('commit','-m',message,'--')
            return {'done':True}
        raise ValueError('Неизвестное действие Git')
