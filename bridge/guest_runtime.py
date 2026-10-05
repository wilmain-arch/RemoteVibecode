"""Linux guest service: private broker, durable queue and owner priority.

External Desktop execution has no proven global activity API. Until one is
available the service fails closed while a Desktop process is present; guests
wait, while owner bridge requests remain usable. No provider requests are used
for capability probes.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
import stat
import threading
import time
from .guest_access import AccessError
from .guest_runner import GuestRunner
from .guest_scheduler import GuestScheduler
from .guest_sandbox import IsolatedExecutor


def desktop_present(*, proc_root=Path('/proc')):
    # Fail closed if /proc cannot be enumerated. Inspect process executable/name,
    # never environment or command-line secrets.
    if sys.platform!='linux':return True
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():continue
            try:
                name=(entry/'comm').read_text().strip().lower()
                executable=str((entry/'exe').resolve()).lower()
            except (FileNotFoundError,ProcessLookupError):continue
            except PermissionError:
                # Other users do not own this account; ignore their processes.
                if entry.stat().st_uid!=os.getuid():continue
                return True
            # Resource helpers are also used by the bridge app-server, even
            # when Desktop is closed. Match the GUI executable, not its directory.
            if entry.stat().st_uid==os.getuid() and (
                Path(executable).name in ('chatgpt','codex-desktop')):return True
        return False
    except OSError:return True


class GuestRuntime:
    def __init__(self,bridge):
        self.bridge=bridge
        self.busy=threading.Event()
        self.stopping=threading.Event()
        self.reason=''
        self.root=bridge.state_file.parent/'guest-runtime'
        self.codex=None
        self.enabled=False
        self.active_runner=None
        self.account_marker=None
        self.worker=threading.Thread(target=self._loop,daemon=True)
        self.worker.start()

    def account_auth(self):
        source=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
        if source.is_symlink() or not source.is_file():raise ValueError('Авторизация Codex Desktop недоступна')
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Некорректный файл авторизации')
            auth=stream.read(1024*1024+1)
        if len(auth)>1024*1024:raise ValueError('Авторизация Codex недействительна')
        data=json.loads(auth)
        tokens=data.get('tokens') if isinstance(data,dict) else None
        identity=tokens.get('account_id') if isinstance(tokens,dict) else None
        if not isinstance(identity,str) or not 1<=len(identity)<=200:
            raise ValueError('Гостевой запуск требует авторизации ChatGPT в Codex Desktop')
        if self.bridge.guests.bind_account(identity):
            self.enabled=False
            with self.bridge.state_lock:
                self.bridge.guest_execution_enabled=False
                self.bridge._save_state()
            raise ValueError('Аккаунт владельца изменился. Гостевой доступ отозван. Перезапустите агент и включите исполнитель заново.')
        return auth,self.bridge.guests.digest(identity)

    def probe(self):
        if sys.platform!='linux' or not shutil.which('bwrap'):
            raise ValueError('Гостевой запуск поддерживается только на Linux с Bubblewrap')
        from .codex_path import resolve_codex_executable
        codex=resolve_codex_executable(os.environ.get('CODEX_EXECUTABLE'))
        if not codex or not Path(codex).resolve().is_relative_to('/usr'):
            raise ValueError('Нужен проверенный Codex Desktop в /usr')
        from websockets.asyncio.server import serve  # dependency availability
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.root.is_symlink():raise ValueError('Папка исполнителя не должна быть ссылкой')
        self.root.chmod(0o700)
        probe=self.root/'probe';probe.mkdir(mode=0o700,exist_ok=True)
        executor=IsolatedExecutor(probe,Path(codex),resource_limits=True)
        executor.close()
        _,self.account_marker=self.account_auth()
        self.codex=Path(codex)

    def configure(self,enabled):
        if not isinstance(enabled,bool):raise ValueError('enabled должен быть логическим значением')
        if enabled:self.probe()
        self.enabled=enabled
        with self.bridge.state_lock:
            self.bridge.guest_execution_enabled=enabled
            self.bridge._save_state()
        return self.status()

    def waiting_reason(self):
        if not self.enabled:return 'Владелец выключил гостевой исполнитель'
        if time.monotonic()<getattr(self.bridge,'update_until',0):return 'Агент обновляется'
        with self.bridge.guests.lock:
            if self.bridge.guests.db.execute("SELECT 1 FROM execution WHERE state='uncertain' LIMIT 1").fetchone() or self.bridge.guests.db.execute("SELECT 1 FROM guest_jobs WHERE state='uncertain' LIMIT 1").fetchone():
                return 'Владелец должен согласовать расход прерванной задачи, чтобы освободить очередь'
        if not self.bridge.paired:return 'Владелец отключил доступ'
        if desktop_present():return 'Ожидание закрытия окна Codex Desktop'
        with self.bridge.turn_lock:
            if self.bridge.active_turns:return 'Ожидание завершения задачи владельца'
        with self.bridge.queue_lock:
            if self.bridge.queued_sends:return 'Ожидание сообщений владельца в очереди'
        return ''

    def status(self):
        return {'enabled':self.enabled,'busy':self.busy.is_set(),'supported':sys.platform=='linux',
            'desktopMustBeClosed':True,'reason':self.waiting_reason() or self.reason}

    def owner_idle(self):
        if not self.bridge.paired or desktop_present():return False
        with self.bridge.turn_lock:
            if self.bridge.active_turns:return False
        return True

    @contextmanager
    def launch_guard(self):
        with self.bridge.send_lock:
            yield

    def can_launch(self):
        if time.monotonic() < getattr(self.bridge, "update_until", 0):return False
        if not self.enabled or not self.bridge.paired or not self.owner_idle():return False
        with self.bridge.queue_lock:
            return not self.bridge.queued_sends

    def provision(self,guest):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',guest):raise ValueError('Некорректная гостевая область')
        base=self.root/guest
        base.mkdir(mode=0o700,exist_ok=True)
        if base.is_symlink():raise ValueError('Гостевая область не должна быть ссылкой')
        work=base/'workspace';broker=base/'broker'
        for path in (work,broker):
            path.mkdir(mode=0o700,exist_ok=True)
            if path.is_symlink():raise ValueError('Некорректная папка гостя')
            path.chmod(0o700)
        # Only the trusted broker receives the owner's auth. It is never mounted
        # in the executor. No owner config, MCP, hooks, plugins or environment.
        auth,marker=self.account_auth()
        self.bridge.guests._guest(guest)
        self.account_marker=marker
        destination=broker/'auth.json'
        if destination.is_symlink():raise ValueError('Некорректный файл авторизации')
        temporary=broker/'auth.pending'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(auth)
        os.replace(temporary,destination)
        return work,broker

    def run(self,task,checkpoint):
        from .server import CodexRpc
        scope=task.get('scopeId','default')
        try:
            if self.bridge.guest_resources.storage_status(task['guestId'])['exhausted']:
                return {'status':'failed','messages':[],'stopReason':'storage_limit'}
            work,broker=self.provision(task['guestId'])
            if scope!='default':work=self.bridge.guest_resources.copy_workspace(task['guestId'],scope)
        except AccessError:
            return {'status':'interrupted','messages':[],'stopReason':'access_revoked' if scope=='default' else 'sharing_revoked'}
        except ValueError:
            return {'status':'failed','messages':[],'stopReason':'setup_failed'}
        conversation=scope+'-'+task['conversationId']
        thread=self.bridge.guest_jobs.conversation(task['guestId'],conversation)
        task={**task,'threadId':thread}
        def started(task,thread,turn):
            if not task.get('threadId'):
                self.bridge.guest_jobs.bind_conversation(task['guestId'],conversation,thread)
            self.bridge.guest_jobs.update(task['operationId'],'running',thread=thread,turn=turn)
        def guarded_checkpoint():
            if self.account_marker is not None:
                _,marker=self.account_auth()
                if marker!=self.account_marker:raise RuntimeError('Аккаунт владельца изменился; гостевой доступ отозван')
            result=checkpoint()
            try:
                if self.bridge.guest_resources.storage_status(task['guestId'])['exhausted']:
                    result['stop']=True;result['stopReason']='storage_limit'
            except AccessError:
                result['stop']=True;result['stopReason']='access_revoked'
            if not self.enabled or self.stopping.is_set():result['stop']=True;result['stopReason']='execution_disabled'
            if scope!='default':
                try:self.bridge.guest_resources.copy_workspace(task['guestId'],scope)
                except Exception:result['stop']=True;result['stopReason']='sharing_revoked'
            return result
        published={'messages':[], 'artifacts':[]}
        def progress(task,messages):
            published['messages']=messages
            self.bridge.guest_jobs.update(task['operationId'],'running',result={'output':dict(published)})
        def artifact_progress(task,artifacts):
            published['artifacts']=artifacts
            self.bridge.guest_jobs.update(task['operationId'],'running',result={'output':dict(published)})
        runner=GuestRunner(workspace=work,broker=broker,codex=self.codex,rpc_factory=CodexRpc,on_started=started,on_progress=progress,on_artifacts=artifact_progress,resource_limits=True)
        self.active_runner=runner
        try:return runner(task,guarded_checkpoint)
        finally:self.active_runner=None

    def _loop(self):
        scheduler=GuestScheduler(self.bridge.guests,self.bridge.guest_jobs,snapshot=self.bridge.guest_usage_limits,
            owner_idle=self.owner_idle,runner=self.run,launch_guard=self.launch_guard,
            can_launch=self.can_launch,on_reserved=self.busy.set)
        while not self.stopping.wait(1):
            if not self.enabled:continue
            try:
                result=scheduler.step()
                self.reason='' if result else self.waiting_reason()
            except Exception:
                self.reason='Задача остановлена или ожидает проверки счётчика. Проверьте очередь.'
            finally:self.busy.clear()

    def close(self):
        self.enabled=False
        self.stopping.set()
        self.worker.join(timeout=15)
        if self.worker.is_alive() and self.active_runner:
            self.active_runner.abort()
            self.worker.join(timeout=5)
