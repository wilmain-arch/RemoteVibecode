"""Session-scoped server requests. Never auto-approve or replay across sessions."""
from __future__ import annotations

import copy
import threading
import time
import uuid

METHODS = {
    'item/tool/requestUserInput', 'item/commandExecution/requestApproval',
    'item/fileChange/requestApproval', 'item/permissions/requestApproval',
    'execCommandApproval', 'applyPatchApproval',
}


class InteractionInbox:
    def __init__(self, send):
        self.send = send
        self.lock = threading.RLock()
        self.pending = {}
        self.completed = {}

    def receive(self, event: dict) -> bool:
        method = event.get('method')
        if method not in METHODS:
            return False
        params = event.get('params') or {}
        with self.lock:
            if len(self.pending) >= 100:
                self.send({'id': event['id'], 'error': {'code': -32000, 'message': 'Too many pending requests'}})
                return True
            if any(item['rpcId'] == event['id'] for item in self.pending.values()):
                return True
            key = uuid.uuid4().hex
            self.pending[key] = {'id': key, 'rpcId': event['id'], 'method': method,
                                 'threadId': params.get('threadId') or params.get('conversationId'),
                                 'createdAt': int(time.time()), 'params': copy.deepcopy(params)}
        return True

    def has_pending(self) -> bool:
        with self.lock:
            return bool(self.pending)

    def resolve(self, rpc_id):
        with self.lock:
            self.pending = {key: item for key, item in self.pending.items() if item['rpcId'] != rpc_id}

    def list(self, thread_id: str, descendants: set[str] | None = None) -> list[dict]:
        with self.lock:
            return [{key: copy.deepcopy(value) for key, value in item.items() if key != 'rpcId'}
                    for item in self.pending.values() if item['threadId'] in ({thread_id} | (descendants or set()))]

    def respond(self, thread_id: str, key: str, response: dict) -> dict:
        if not isinstance(key, str) or len(key) != 32:
            raise ValueError("Некорректный ID запроса")
        with self.lock:
            prior = self.completed.get(key)
            if prior:
                if prior['threadId'] != thread_id or prior['response'] != response:
                    raise ValueError('Этот запрос уже получил другой ответ')
                return {'accepted': True}
            item = self.pending.get(key)
            if not item or item['threadId'] != thread_id:
                raise ValueError('Запрос уже закрыт или относится к другой сессии')
            result = self.validate(item, response)
            self.send({'id': item['rpcId'], 'result': result})
            self.pending.pop(key, None)
            self.completed[key] = {'threadId': thread_id, 'response': copy.deepcopy(response)}
            while len(self.completed) > 200:
                self.completed.pop(next(iter(self.completed)))
            return {'accepted': True}

    @staticmethod
    def validate(item: dict, response: dict) -> dict:
        if not isinstance(response, dict):
            raise ValueError('Ожидался ответ')
        method, params = item['method'], item['params']
        if method == 'item/tool/requestUserInput':
            answers = response.get('answers')
            questions = params.get('questions') or []
            if not isinstance(answers, dict) or set(answers) != {q['id'] for q in questions}:
                raise ValueError('Ответьте на все вопросы')
            result = {}
            for question in questions:
                value = answers[question['id']]
                texts = value.get('answers') if isinstance(value, dict) else None
                if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str) or not texts[0].strip() or len(texts[0]) > 32000:
                    raise ValueError('Введите ответ до 32 000 символов')
                options = question.get('options') or []
                if options and not question.get('isOther') and texts[0] not in {o['label'] for o in options}:
                    raise ValueError('Выберите предложенный вариант')
                result[question['id']] = {'answers': texts}
            return {'answers': result}
        if method == 'item/permissions/requestApproval':
            if response.get('decision') not in ('accept', 'decline'):
                raise ValueError('Неизвестное решение')
            # Only exactly the requested permissions, only for this turn.
            permissions = {k: v for k, v in (params.get('permissions') or {}).items() if v is not None}
            return {'permissions': permissions if response['decision'] == 'accept' else {}, 'scope': 'turn'}
        decision = response.get('decision')
        allowed = {'accept', 'decline', 'cancel'}
        advertised = params.get('availableDecisions')
        if isinstance(advertised, list):
            allowed &= {d for d in advertised if isinstance(d, str)}
        if decision not in allowed:
            raise ValueError('Это решение недоступно для запроса')
        if method in ('execCommandApproval', 'applyPatchApproval'):
            decision = {'accept': 'approved', 'decline': {'denied': {'rejection': 'Отклонено пользователем'}},
                        'cancel': 'abort'}[decision]
        return {'decision': decision}
