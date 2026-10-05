"""Linux guest executor: no host home, credentials, network or PIDs.

Used by the guest gateway and the capability probe. A successful probe alone
does not prove provider availability or all model tools.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading


class IsolatedExecutor:
    def __init__(self, workspace: Path, codex: Path, *, initialize=True, resource_limits=False):
        if sys.platform != 'linux' or not shutil.which('bwrap'):
            raise RuntimeError('Гостевой исполнитель требует Linux и Bubblewrap')
        workspace=Path(workspace)
        codex=Path(codex)
        if workspace.is_symlink() or not workspace.is_dir() or not codex.is_file():
            raise ValueError('Некорректная рабочая папка или Codex')
        workspace=workspace.resolve()
        codex=codex.resolve()
        if not codex.is_relative_to('/usr'):
            raise ValueError('Проверенный исполнитель должен находиться в /usr')
        argv=['bwrap','--die-with-parent','--new-session','--unshare-all','--cap-drop','ALL',
              '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin',
              '--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64',
              '--proc','/proc','--dev','/dev','--size','67108864','--tmpfs','/tmp',
              '--dir','/etc','--dir','/home','--size','67108864','--tmpfs','/home/guest',
              '--bind',str(workspace),'/workspace','--chdir','/workspace',
              '--clearenv','--setenv','HOME','/home/guest',
              '--setenv','CODEX_HOME','/home/guest/.codex',
              '--setenv','PATH','/usr/bin:/bin',
              '--',str(codex),'exec-server','--listen','stdio']
        if resource_limits:
            if not shutil.which('prlimit'):raise RuntimeError('Нужен prlimit для ограничений гостевых файлов')
            argv=['prlimit','--fsize=67108864:67108864','--core=0:0','--',*argv]
            if not shutil.which('systemd-run'):raise RuntimeError('Нужен systemd-run для лимитов гостевой среды')
            argv=['systemd-run','--user','--scope','--quiet','--collect',
                  '--property=MemoryMax=1G','--property=TasksMax=128','--property=CPUQuota=100%',*argv]
        self.proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                   text=True,bufsize=1,start_new_session=True)
        self.incoming=queue.Queue(maxsize=128)
        self.serial=0
        self.lock=threading.Lock()
        threading.Thread(target=self._read,daemon=True).start()
        try:
            if initialize:
                self.call('initialize',{'clientName':'RemoteVibecode-guest-isolation-probe'})
                self.notify('initialized',{})
        except BaseException:
            self.close()
            raise

    def _read(self):
        while True:
            line=self.proc.stdout.readline(8*1024*1024+1)
            if not line:break
            if len(line)>8*1024*1024:
                break
            try:self.incoming.put(json.loads(line),timeout=1)
            except (ValueError,queue.Full):break
        try:self.incoming.put_nowait({'closed':True})
        except queue.Full:pass

    def notify(self,method,params):
        self.proc.stdin.write(json.dumps({'method':method,'params':params})+'\n')
        self.proc.stdin.flush()

    def call(self,method,params):
        with self.lock:
            self.serial+=1
            ident=self.serial
            self.proc.stdin.write(json.dumps({'id':ident,'method':method,'params':params})+'\n')
            self.proc.stdin.flush()
            while True:
                try:message=self.incoming.get(timeout=5)
                except queue.Empty:raise RuntimeError('Изолированный исполнитель не ответил') from None
                if message.get('closed'):raise RuntimeError('Изолированный исполнитель остановлен')
                if message.get('id')!=ident:continue
                if 'error' in message:raise RuntimeError('Исполнитель отклонил запрос: '+str(message['error'].get('message','')))
                return message.get('result',{})

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        for stream in (self.proc.stdin,self.proc.stdout):
            if stream:stream.close()
