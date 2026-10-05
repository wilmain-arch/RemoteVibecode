"""Guest identities, grants and independent durable quota budgets.

The store never authorizes a legacy bridge route. Execution requires a separately
verified runner; account credentials are never issued to a guest.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import uuid

WINDOWS = ('fiveHours', 'week')


class AccessError(ValueError):
    pass


def quota_rule(value):
    if not isinstance(value, dict):
        raise ValueError('Ожидалось правило квоты')
    mode = value.get('mode')
    if mode not in ('unlimited', 'fixed', 'renewing'):
        raise ValueError('Неизвестный режим квоты')
    if mode == 'unlimited':
        return {'mode': mode}
    basis = value.get('basis')
    amount = value.get('amount')
    if basis not in ('full', 'remaining'):
        raise ValueError('Неизвестная база квоты')
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= 100:
        raise ValueError('Размер квоты должен быть больше 0 и не больше 100')
    return {'mode': mode, 'basis': basis, 'amount': float(amount)}


def snapshot_window(snapshot, key):
    value = snapshot.get(key) if isinstance(snapshot, dict) else None
    if not isinstance(value, dict):
        raise AccessError('Счётчик Codex недоступен')
    remaining, epoch = value.get('remainingPercent'), value.get('resetsAt')
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining) or not 0 <= remaining <= 100:
        raise AccessError('Недействительный счётчик Codex')
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
        raise AccessError('Время сброса Codex неизвестно')
    return float(remaining), epoch


class GuestAccess:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS guests(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                status TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY, guest TEXT NOT NULL REFERENCES guests(id),
                digest TEXT UNIQUE NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY, guest TEXT NOT NULL REFERENCES guests(id),
                device TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(guest TEXT NOT NULL REFERENCES guests(id), kind TEXT NOT NULL,
                resource TEXT NOT NULL, right TEXT NOT NULL, PRIMARY KEY(guest,kind,resource));
            CREATE TABLE IF NOT EXISTS budgets(guest TEXT NOT NULL REFERENCES guests(id), window TEXT NOT NULL,
                rule TEXT NOT NULL, allocated REAL NOT NULL, spent REAL NOT NULL, epoch INTEGER,
                PRIMARY KEY(guest,window));
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS execution(id TEXT PRIMARY KEY, guest TEXT NOT NULL REFERENCES guests(id),
                before_sample TEXT NOT NULL, state TEXT NOT NULL, after_sample TEXT);
        ''')

    def bind_owner(self, token):
        """Changing the owner's pairing identity permanently invalidates old guests."""
        owner_hash=self.digest(token)
        def run():
            previous=self.db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()
            if previous and previous[0]!=owner_hash:
                self.db.execute("UPDATE guests SET status='revoked'")
                self.db.execute('DELETE FROM sessions')
                self.db.execute('DELETE FROM grants')
                self.db.execute('UPDATE invitations SET expires=0')
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('owner',?)",(owner_hash,))
        return self._transaction(run)

    def bind_account(self,identity):
        if not isinstance(identity,str) or not 1<=len(identity)<=200:raise AccessError('Идентичность аккаунта недоступна')
        account_hash=self.digest(identity)
        def run():
            previous=self.db.execute("SELECT value FROM metadata WHERE key='account'").fetchone()
            changed=bool(previous and previous[0]!=account_hash)
            if changed:
                self.db.execute("UPDATE guests SET status='revoked'")
                self.db.execute('DELETE FROM sessions')
                self.db.execute('DELETE FROM grants')
                self.db.execute('UPDATE invitations SET expires=0')
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('account',?)",(account_hash,))
            return changed
        return self._transaction(run)

    @staticmethod
    def digest(secret):
        return hashlib.sha256(secret.encode('utf-8')).hexdigest()

    def _transaction(self, callback):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result = callback()
                self.db.execute('COMMIT')
                return result
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def _guest(self, guest):
        row = self.db.execute('SELECT name,status FROM guests WHERE id=?', (guest,)).fetchone()
        if not row or row[1] == 'revoked':
            raise AccessError('Доступ гостя отозван')
        return row

    def _operation(self, operation, kind, payload, callback):
        if not isinstance(operation, str) or not 8 <= len(operation) <= 100:
            raise ValueError('Нужен стабильный operationId')
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        def run():
            row = self.db.execute('SELECT kind,payload,result FROM operations WHERE id=?', (operation,)).fetchone()
            if row:
                if row[0] != kind or row[1] != serialized:
                    raise ValueError('operationId уже используется другим запросом')
                return json.loads(row[2])
            result = callback()
            self.db.execute('INSERT INTO operations VALUES(?,?,?,?)',
                            (operation, kind, serialized, json.dumps(result, ensure_ascii=False)))
            return result
        return self._transaction(run)

    def _set_budget(self, guest, key, rule, snapshot, fresh=False):
        row = self.db.execute('SELECT spent FROM budgets WHERE guest=? AND window=?', (guest,key)).fetchone()
        spent = 0.0 if fresh or not row else row[0]
        allocated, epoch = 0.0, None
        if rule['mode'] != 'unlimited':
            snapshot = snapshot() if callable(snapshot) else snapshot
            remaining, epoch = snapshot_window(snapshot, key)
            allocated = rule['amount'] * remaining / 100 if rule['basis'] == 'remaining' else rule['amount']
        self.db.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?,?,?,?)',
                        (guest,key,json.dumps(rule),allocated,spent,epoch))

    def invite(self, data, snapshot):
        name = data.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Имя гостя: от 1 до 80 символов')
        quotas=data.get('quotas') or {}
        if not isinstance(quotas,dict) or any(key not in WINDOWS for key in quotas):
            raise ValueError('Некорректные окна квоты')
        rules = {key: quota_rule(quotas.get(key, {'mode':'unlimited'})) for key in WINDOWS}
        ttl = data.get('ttlSeconds', 1800)
        if isinstance(ttl,bool) or not isinstance(ttl,int) or not 60 <= ttl <= 86400:
            raise ValueError('Срок приглашения: от минуты до суток')
        secret = data.get('secret')
        if not isinstance(secret, str) or not 40 <= len(secret) <= 128:
            raise ValueError('Нужен случайный ключ приглашения')
        def create():
            guest, invite = uuid.uuid4().hex, uuid.uuid4().hex
            self.db.execute('INSERT INTO guests VALUES(?,?,?,?)', (guest,name.strip(),'invited',time.time()))
            self.db.execute('INSERT INTO invitations VALUES(?,?,?,?,0)', (invite,guest,self.digest(secret),time.time()+ttl))
            for key in WINDOWS:
                self._set_budget(guest,key,rules[key],snapshot,True)
            return {'guestId':guest,'inviteId':invite,'expiresAt':int(time.time()+ttl)}
        # Response contains an invitation capability, never a guest session or owner token.
        return self._operation(data.get('operationId'), 'invite', {'name':name,'quotas':rules,'ttl':ttl,'secretHash':self.digest(secret)}, create)

    def redeem(self, secret, device, session_token):
        if not isinstance(secret,str) or len(secret)>128 or not isinstance(device,str) or not 8<=len(device)<=100:
            raise ValueError('Недействительное приглашение или устройство')
        if not isinstance(session_token,str) or not 40<=len(session_token)<=128:
            raise ValueError('Нужен случайный ключ сессии')
        digest = self.digest(secret)
        token_hash = self.digest(session_token)
        def run():
            row = self.db.execute('SELECT guest,expires,consumed FROM invitations WHERE digest=?',(digest,)).fetchone()
            if not row:
                raise AccessError('Приглашение недействительно или истекло')
            self._guest(row[0])
            old = self.db.execute('SELECT guest,device,expires FROM sessions WHERE digest=?',(token_hash,)).fetchone()
            if row[2]:
                if not old or old[0]!=row[0] or old[1]!=device or old[2]<=time.time():
                    raise AccessError('Приглашение уже использовано')
            else:
                if row[1]<=time.time():raise AccessError('Приглашение недействительно или истекло')
                if old:
                    raise AccessError('Ключ сессии уже используется')
                self.db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token_hash,row[0],device,time.time()+30*86400))
                self.db.execute('UPDATE invitations SET consumed=1 WHERE digest=?',(digest,))
                self.db.execute("UPDATE guests SET status='active' WHERE id=?",(row[0],))
            return {'guestId':row[0], 'role':'guest', 'executionAvailable':False,
                    'executionReason':'Изолированный исполнитель ещё не подключён'}
        return self._transaction(run)

    def authenticate(self, token):
        with self.lock:
            row=self.db.execute('SELECT guest FROM sessions WHERE digest=? AND expires>?',(self.digest(token),time.time())).fetchone()
            if not row:
                raise AccessError('Сессия гостя недействительна')
            self._guest(row[0])
            return row[0]

    def view(self, guest):
        with self.lock:
            name,status=self._guest(guest)
            budgets={}
            for key,rule,allocated,spent,epoch in self.db.execute('SELECT window,rule,allocated,spent,epoch FROM budgets WHERE guest=?',(guest,)):
                rules=json.loads(rule)
                budgets[key]={'rule':rules,'allocated':allocated,'spent':spent,
                              'remaining':None if rules['mode']=='unlimited' else max(0,allocated-spent),'resetsAt':epoch}
            grants=[{'kind':k,'resourceId':r,'right':p} for k,r,p in self.db.execute('SELECT kind,resource,right FROM grants WHERE guest=?',(guest,))]
            return {'id':guest,'name':name,'status':status,'quotas':budgets,'grants':grants,
                    'workspaceScopeId':'guest-'+guest,'executionAvailable':False}

    def list(self):
        with self.lock:
            return {'guests':[self.view(r[0]) for r in self.db.execute("SELECT id FROM guests WHERE status!='revoked' ORDER BY created DESC").fetchall()],
                    'executionAvailable':False,'executionReason':'Изолированный исполнитель ещё не подключён'}

    def change(self, data, snapshot):
        guest=data.get('guestId')
        if not isinstance(guest,str) or len(guest)!=32:
            raise ValueError('Недействительный ID гостя')
        quotas=data.get('quotas') or {}
        if not isinstance(quotas,dict):
            raise ValueError('Некорректные квоты')
        rules={key:quota_rule(value) for key,value in quotas.items()}
        if any(k not in WINDOWS for k in rules):
            raise ValueError('Неизвестное окно квоты')
        action=data.get('action')
        if action not in ('configure','reallocate','revoke','grant','unshare'):
            raise ValueError('Неизвестное действие гостевого доступа')
        if action in ('grant','unshare'):
            if data.get('kind') not in ('thread','project') or not isinstance(data.get('resourceId'),str) or not 1<=len(data['resourceId'])<=512:
                raise ValueError('Недействительный ресурс')
            if action=='grant' and data.get('right') not in ('view','work'):
                raise ValueError('Недействительное право')
        def run():
            self._guest(guest)
            if action=='revoke':
                self.db.execute("UPDATE guests SET status='revoked' WHERE id=?",(guest,))
                self.db.execute('DELETE FROM sessions WHERE guest=?',(guest,))
                self.db.execute('DELETE FROM grants WHERE guest=?',(guest,))
                return {'revoked':True,'guestId':guest}
            if action in ('configure','reallocate'):
                if not rules:
                    raise ValueError('Выберите окно квоты')
                if action=='reallocate' and self.db.execute("SELECT 1 FROM execution WHERE guest=? AND state IN ('active','uncertain') LIMIT 1",(guest,)).fetchone():
                    raise ValueError('Сначала завершите задачу или согласуйте её расход, затем выделяйте квоту заново')
                for key,rule in rules.items():
                    self._set_budget(guest,key,rule,snapshot,fresh=action=='reallocate')
            elif action=='grant':
                self.db.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?,?)',(guest,data['kind'],data['resourceId'],data['right']))
            elif action=='unshare':
                self.db.execute('DELETE FROM grants WHERE guest=? AND kind=? AND resource=?',(guest,data['kind'],data['resourceId']))
            return self.view(guest)
        stored={key:data[key] for key in ('guestId','action','kind','resourceId','right') if key in data}
        stored['quotas']=rules
        return self._operation(data.get('operationId'),action,stored,run)

    def synchronize_idle(self,guest,snapshot):
        def run():
            self._guest(guest)
            if self.db.execute("SELECT 1 FROM execution WHERE guest=? AND state IN ('active','uncertain') LIMIT 1",(guest,)).fetchone():return False
            self._synchronize(guest,snapshot)
            return True
        return self._transaction(run)

    def synchronize(self, guest, snapshot):
        return self._transaction(lambda: self._synchronize(guest, snapshot))

    def _synchronize(self, guest, snapshot):
        """Renew only the window whose reset epoch advanced; fixed spent is lifetime."""
        with self.lock:
            self._guest(guest)
            for key,raw,allocated,spent,epoch in self.db.execute('SELECT window,rule,allocated,spent,epoch FROM budgets WHERE guest=?',(guest,)).fetchall():
                rule=json.loads(raw)
                if rule['mode']=='unlimited':
                    continue
                remaining,new_epoch=snapshot_window(snapshot,key)
                if new_epoch < epoch:
                    raise AccessError('Получен устаревший счётчик Codex')
                if new_epoch != epoch:
                    if rule['mode']=='renewing':
                        allocated=rule['amount']*remaining/100 if rule['basis']=='remaining' else rule['amount']
                        spent=0
                    self.db.execute('UPDATE budgets SET allocated=?,spent=?,epoch=? WHERE guest=? AND window=?', (allocated,spent,new_epoch,guest,key))

    def begin(self, guest, operation, snapshot, external_active=False):
        """Reserve the single measurement lane before a runner starts a task."""
        def run():
            self._guest(guest)
            previous=self.db.execute('SELECT guest,state FROM execution WHERE id=?',(operation,)).fetchone()
            if previous:
                if previous[0]!=guest:
                    raise AccessError('Задача принадлежит другому гостю')
                return {'operationId':operation,'state':previous[1],'replayed':True}
            if external_active or self.db.execute("SELECT 1 FROM execution WHERE state IN ('active','uncertain') LIMIT 1").fetchone():
                raise AccessError('Дождитесь завершения другой задачи и согласования расхода')
            self._synchronize(guest,snapshot)
            for key in WINDOWS:
                remaining,_=snapshot_window(snapshot,key)
                if remaining<=0:
                    raise AccessError('Общий лимит Codex исчерпан')
            for rule,allocated,spent in self.db.execute('SELECT rule,allocated,spent FROM budgets WHERE guest=?',(guest,)):
                if json.loads(rule)['mode']!='unlimited' and spent>=allocated:
                    raise AccessError('Квота гостя исчерпана')
            self.db.execute('INSERT INTO execution VALUES(?,?,?,\'active\',NULL)',(operation,guest,json.dumps(snapshot)))
            return {'operationId':operation,'state':'active','replayed':False}
        return self._transaction(run)

    def checkpoint(self, operation, snapshot, interference=False):
        """Charge only the delta since the last accepted sample; fail closed."""
        return self._transaction(lambda: self._measure(operation, snapshot, interference, False))

    def finish(self, operation, snapshot, interference=False):
        return self._transaction(lambda: self._measure(operation, snapshot, interference, True))

    def _measure(self, operation, snapshot, interference, final):
        row=self.db.execute('SELECT guest,before_sample,state,after_sample FROM execution WHERE id=?',(operation,)).fetchone()
        if not row:
            raise ValueError('Неизвестная задача')
        if row[2]=='completed':
            return {'state':'completed','replayed':True}
        if row[2]=='uncertain':
            return {'state':'uncertain','reason':'Требуется явное согласование владельцем'}
        before=json.loads(row[3] or row[1])
        original=json.loads(row[1])
        deltas={}
        total={}
        for key in WINDOWS:
            old,old_epoch=snapshot_window(before,key)
            new,new_epoch=snapshot_window(snapshot,key)
            if interference or new_epoch!=old_epoch or new>old:
                self.db.execute("UPDATE execution SET state='uncertain' WHERE id=?",(operation,))
                return {'state':'uncertain','reason':'Расход невозможно достоверно разделить'}
            deltas[key]=old-new
            total[key]=snapshot_window(original,key)[0]-new
        for key,amount in deltas.items():
            self.db.execute('UPDATE budgets SET spent=spent+? WHERE guest=? AND window=?',(amount,row[0],key))
        state='completed' if final else 'active'
        self.db.execute('UPDATE execution SET state=?,after_sample=? WHERE id=?',(state,json.dumps(snapshot),operation))
        exhausted=any(snapshot_window(snapshot,key)[0]<=0 for key in WINDOWS)
        exhausted=exhausted or any(json.loads(rule)['mode']!='unlimited' and spent>=allocated
            for rule,allocated,spent in self.db.execute('SELECT rule,allocated,spent FROM budgets WHERE guest=?',(row[0],)))
        return {'state':state,'usage':total,'delta':deltas,'quotaExhausted':exhausted}
