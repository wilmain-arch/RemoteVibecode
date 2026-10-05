"""Durable guest queue, bound to the same database and identities as quotas."""
import json
import math
import re
import time
try:
    from .guest_access import AccessError
except ImportError:
    from guest_access import AccessError

ID=re.compile(r'[a-zA-Z0-9_-]{8,100}')


class GuestJobs:
    def __init__(self, access):
        self.access=access
        with access.lock:
            access.db.executescript('''
                CREATE TABLE IF NOT EXISTS guest_jobs(id TEXT PRIMARY KEY,guest TEXT NOT NULL REFERENCES guests(id),
                    payload TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,thread TEXT,turn TEXT,
                    result TEXT NOT NULL DEFAULT '{}',reason TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS guest_cancellations(id TEXT PRIMARY KEY,guest TEXT NOT NULL REFERENCES guests(id));
                CREATE TABLE IF NOT EXISTS guest_conversations(guest TEXT NOT NULL REFERENCES guests(id),
                    id TEXT NOT NULL,thread TEXT NOT NULL,PRIMARY KEY(guest,id));
            ''')
            columns={row[1] for row in access.db.execute('PRAGMA table_info(guest_jobs)')}
            if 'eligible' not in columns:
                access.db.execute('ALTER TABLE guest_jobs ADD COLUMN eligible REAL NOT NULL DEFAULT 0')
            for column in ('started','completed'):
                if column not in columns:access.db.execute(f'ALTER TABLE guest_jobs ADD COLUMN {column} REAL')

    def defer(self,op,delay=30):
        self.update(op,'queued',reason='waiting_for_budget_or_meter')
        self.access._transaction(lambda:self.access.db.execute('UPDATE guest_jobs SET eligible=? WHERE id=?',(time.time()+delay,op)))

    @staticmethod
    def _request(data):
        op=data.get('operationId');text=data.get('text');conversation=data.get('conversationId','main')
        if not isinstance(op,str) or not ID.fullmatch(op):raise ValueError('Нужен стабильный operationId')
        if not isinstance(text,str) or not text.strip() or len(text)>100000:raise ValueError('Сообщение: от 1 до 100000 символов')
        if not isinstance(conversation,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',conversation):raise ValueError('Некорректный чат')
        scope=data.get('scopeId','default')
        if not isinstance(scope,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',scope):raise ValueError('Некорректная рабочая область')
        model=data.get('model');effort=data.get('effort')
        if model is not None and (not isinstance(model,str) or not re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,100}',model)):raise ValueError('Некорректная модель')
        if effort is not None and effort not in ('none','minimal','low','medium','high','xhigh','max','ultra'):raise ValueError('Некорректное усилие')
        payload=json.dumps({'text':text,'conversationId':conversation,'scopeId':scope,'model':model,'effort':effort},sort_keys=True,ensure_ascii=False)
        return op,payload

    def replay(self,guest,data):
        op,payload=self._request(data)
        with self.access.lock:
            self.access._guest(guest)
            old=self.access.db.execute('SELECT guest,payload FROM guest_jobs WHERE id=?',(op,)).fetchone()
            if old:
                if old!=(guest,payload):raise AccessError('operationId уже используется другим запросом')
                return self.get(guest,op)
            return None

    def enqueue(self,guest,data):
        op,payload=self._request(data)
        def run():
            _,status=self.access._guest(guest)
            if status!='active':raise AccessError('Сначала примите приглашение')
            old=self.access.db.execute('SELECT guest,payload FROM guest_jobs WHERE id=?',(op,)).fetchone()
            if old:
                if old!=(guest,payload):raise AccessError('operationId уже используется другим запросом')
                return self.get(guest,op)
            cancelled=self.access.db.execute('SELECT guest FROM guest_cancellations WHERE id=?',(op,)).fetchone()
            if cancelled and cancelled[0]!=guest:raise AccessError('operationId уже используется другим запросом')
            count=self.access.db.execute("SELECT count(*) FROM guest_jobs WHERE guest=? AND state IN ('queued','dispatching','running','cancel_requested')",(guest,)).fetchone()[0]
            if count>=20 and not cancelled:raise ValueError('Очередь гостя заполнена')
            self.access.db.execute('INSERT INTO guest_jobs(id,guest,payload,state,created) VALUES(?,?,?,\'queued\',?)',(op,guest,payload,time.time()))
            if cancelled:
                self.access.db.execute("UPDATE guest_jobs SET state='cancelled',reason='cancelled_by_guest',completed=? WHERE id=?",(time.time(),op))
                self.access.db.execute('DELETE FROM guest_cancellations WHERE id=?',(op,))
            return self.get(guest,op)
        return self.access._transaction(run)

    def get(self,guest,op):
        with self.access.lock:
            row=self.access.db.execute('SELECT id,guest,payload,state,thread,turn,result,reason,created,started,completed FROM guest_jobs WHERE id=?',(op,)).fetchone()
            if not row or row[1]!=guest:raise AccessError('Задача недоступна')
            return {'operationId':row[0],**json.loads(row[2]),'state':row[3],'threadId':row[4],'turnId':row[5],
                    'result':json.loads(row[6]),'reason':row[7],'created':row[8],'startedAt':row[9],'completedAt':row[10]}

    def list(self,guest,before=None):
        with self.access.lock:
            self.access._guest(guest)
            if before:
                point=self.access.db.execute('SELECT created,id FROM guest_jobs WHERE guest=? AND id=?',(guest,before)).fetchone()
                if not point:raise ValueError('Недействительная граница истории')
                ids=self.access.db.execute('SELECT id FROM guest_jobs WHERE guest=? AND (created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT 101',(guest,point[0],point[0],point[1])).fetchall()
            else:
                ids=self.access.db.execute('SELECT id FROM guest_jobs WHERE guest=? ORDER BY created DESC,id DESC LIMIT 101',(guest,)).fetchall()
            page=ids[:100]
            return {'tasks':[self.get(guest,row[0]) for row in page],
                'nextCursor':page[-1][0] if len(ids)>100 else None}

    def claim(self):
        def run():
            self._reconcile_revoked()
            if self.access.db.execute("SELECT 1 FROM execution WHERE state IN ('active','uncertain') LIMIT 1").fetchone():return None
            if self.access.db.execute("SELECT 1 FROM guest_jobs WHERE state IN ('dispatching','running','cancel_requested','uncertain') LIMIT 1").fetchone():return None
            row=self.access.db.execute("SELECT j.id,j.guest FROM guest_jobs j JOIN guests g ON g.id=j.guest WHERE j.state='queued' AND g.status='active' AND j.eligible<=? ORDER BY j.created LIMIT 1",(time.time(),)).fetchone()
            if not row:return None
            self.access.db.execute("UPDATE guest_jobs SET state='dispatching' WHERE id=?",(row[0],))
            return {'guestId':row[1],**self.get(row[1],row[0])}
        return self.access._transaction(run)

    def update(self,op,state,*,thread=None,turn=None,result=None,reason=''):
        if state not in ('queued','running','completed','failed','cancelled','cancel_requested','uncertain'):raise ValueError('Недействительное состояние задачи')
        def run():
            old=self.access.db.execute('SELECT state FROM guest_jobs WHERE id=?',(op,)).fetchone()
            if not old:raise ValueError('Неизвестная задача')
            # A revoke/cancel race cannot be undone by a late start acknowledgement.
            if state in ('running','queued') and old[0] in ('cancelled','cancel_requested'):
                state_value=old[0]
            else:state_value=state
            if turn is not None:self.access.db.execute('UPDATE guest_jobs SET started=coalesce(started,?) WHERE id=?',(time.time(),op))
            if state_value in ('completed','failed','cancelled','uncertain'):
                self.access.db.execute('UPDATE guest_jobs SET completed=coalesce(completed,?) WHERE id=?',(time.time(),op))
            self.access.db.execute('UPDATE guest_jobs SET state=?,thread=coalesce(?,thread),turn=coalesce(?,turn),result=coalesce(?,result),reason=? WHERE id=?',
                (state_value,thread,turn,None if result is None else json.dumps(result,ensure_ascii=False),reason[:200],op))
        return self.access._transaction(run)

    def cancel(self,guest,op):
        return self.access._transaction(lambda: self._cancel_locked(guest,op))

    def _cancel_locked(self,guest,op):
        self.access._guest(guest)
        if not isinstance(op,str) or not ID.fullmatch(op):raise ValueError('Нужен стабильный operationId')
        if not self.access.db.execute('SELECT 1 FROM guest_jobs WHERE id=?',(op,)).fetchone():
            old=self.access.db.execute('SELECT guest FROM guest_cancellations WHERE id=?',(op,)).fetchone()
            if old and old[0]!=guest:raise AccessError('Задача недоступна')
            if not old and self.access.db.execute('SELECT count(*) FROM guest_cancellations WHERE guest=?',(guest,)).fetchone()[0]>=1000:
                raise ValueError('Слишком много отменённых неподтверждённых запросов')
            self.access.db.execute('INSERT OR IGNORE INTO guest_cancellations VALUES(?,?)',(op,guest))
            return {'operationId':op,'state':'cancelled','reason':'cancelled_by_guest'}
        task=self.get(guest,op)
        if task['state']=='queued':
            self.access.db.execute("UPDATE guest_jobs SET state='cancelled',reason='cancelled_by_guest',completed=? WHERE id=?",(time.time(),op))
        elif task['state'] in ('dispatching','running'):
            self.access.db.execute("UPDATE guest_jobs SET state='cancel_requested',reason='cancelled_by_guest' WHERE id=?",(op,))
        return self.get(guest,op)

    def revoke(self,guest):
        def run():
            self.access.db.execute("UPDATE guest_jobs SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,reason='access_revoked' WHERE guest=? AND state IN ('queued','dispatching','running')",(guest,))
        self.access._transaction(run)

    def _reconcile_revoked(self):
        self.access.db.execute("UPDATE guest_jobs SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,reason='access_revoked' WHERE state IN ('queued','dispatching','running') AND guest IN (SELECT id FROM guests WHERE status='revoked')")

    def recover(self):
        def run():
            self._reconcile_revoked()
            self.access.db.execute("UPDATE guest_jobs SET state='uncertain',reason='bridge_restarted' WHERE state IN ('dispatching','running','cancel_requested')")
            self.access.db.execute("UPDATE execution SET state='uncertain' WHERE state='active'")
        self.access._transaction(run)

    def conversation(self,guest,conversation):
        with self.access.lock:
            row=self.access.db.execute('SELECT thread FROM guest_conversations WHERE guest=? AND id=?',(guest,conversation)).fetchone()
            return row[0] if row else None

    def bind_conversation(self,guest,conversation,thread):
        self.access._transaction(lambda:self.access.db.execute('INSERT INTO guest_conversations VALUES(?,?,?)',(guest,conversation,thread)))

    def owner_list(self):
        with self.access.lock:
            rows=self.access.db.execute("SELECT id,guest FROM guest_jobs ORDER BY (state='uncertain') DESC,created DESC LIMIT 100").fetchall()
            return {'tasks':[{'guestId':guest,**self.get(guest,op)} for op,guest in rows]}

    def resolve(self,data):
        op=data.get('operationId');amounts=data.get('additionalUsage')
        if data.get('confirmed') is not True or not isinstance(amounts,dict):
            raise ValueError('Подтвердите согласование расхода')
        values={}
        for key in ('fiveHours','week'):
            value=amounts.get(key)
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not 0<=value<=100:
                raise ValueError('Дополнительный расход: от 0 до 100 процентных пунктов для каждого окна')
            values[key]=value
        def run():
            task=self.access.db.execute('SELECT guest,state,result FROM guest_jobs WHERE id=?',(op,)).fetchone()
            meter=self.access.db.execute('SELECT state FROM execution WHERE id=?',(op,)).fetchone()
            previous=json.loads(task[2]) if task else {}
            if task and task[1]=='failed' and previous.get('ownerResolved'):
                if previous.get('additionalUsage')!=values:raise ValueError('Расход уже согласован с другими значениями')
                return self.get(task[0],op)
            if not task or task[1]!='uncertain' or (meter and meter[0]!='uncertain'):
                raise ValueError('Задача не ожидает согласования')
            for key,value in values.items():
                self.access.db.execute('UPDATE budgets SET spent=spent+? WHERE guest=? AND window=?',(value,task[0],key))
            if meter:
                self.access.db.execute("UPDATE execution SET state='completed' WHERE id=?",(op,))
            else:
                # Recovery may find a durable dispatch without a meter row.
                # Explicit owner reconciliation releases it without replay.
                self.access.db.execute("INSERT INTO execution VALUES(?,?,?,'completed',?)",(op,task[0],'{}',json.dumps({'ownerResolved':True,'additionalUsage':values})))
            self.access.db.execute("UPDATE guest_jobs SET state='failed',reason='resolved_by_owner',result=? WHERE id=?",(json.dumps({**previous,'ownerResolved':True,'additionalUsage':values}),op))
            return self.get(task[0],op)
        return self.access._transaction(run)

    def cancel_scope(self,guest,scope):
        def run():
            rows=self.access.db.execute("SELECT id,payload,state FROM guest_jobs WHERE guest=? AND state IN ('queued','dispatching','running')",(guest,)).fetchall()
            for op,payload,state in rows:
                if json.loads(payload).get('scopeId','default')==scope:
                    self.access.db.execute("UPDATE guest_jobs SET state=?,reason='sharing_revoked' WHERE id=?",('cancelled' if state=='queued' else 'cancel_requested',op))
        self.access._transaction(run)
