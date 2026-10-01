"""Signed GitHub updates, isolated from bridge connectivity and credentials."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib import request, error
from urllib.parse import urlparse
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

REPO = 'wilmain-arch/RemoteVibecode'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
VERSION = '0.2.3'
DAY = 86400
MAX_FILE = 300 * 1024 * 1024

def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Неподдерживаемая версия обновления')
    return tuple(map(int, value.split('.')))

def trusted_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or p.netloc != 'github.com' or not p.path.startswith(f'/{REPO}/releases/download/bundle-v') or p.query or p.fragment:
        raise ValueError('Неизвестный источник обновления')
    return url

def fetch(url, limit):
    req = request.Request(url, headers={'User-Agent':'RemoteVibecode-updater','Accept':'application/vnd.github+json' if url == API else 'application/octet-stream'})
    try:
        with request.urlopen(req, timeout=30) as response:
            if urlparse(response.url).scheme != 'https':
                raise ValueError('Небезопасное перенаправление')
            body = response.read(limit + 1)
    except error.HTTPError as exc:
        if exc.code in (403,429):
            raise RuntimeError('GitHub ограничил запросы. Попробуйте позже.') from exc
        raise RuntimeError(f'GitHub недоступен (HTTP {exc.code})') from exc
    if len(body) > limit:
        raise ValueError('Файл обновления слишком большой')
    return body

def check():
    release = json.loads(fetch(API, 2 * 1024 * 1024))
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Ожидался стабильный релиз')
    assets = {x['name']: x['browser_download_url'] for x in release.get('assets', [])}
    if not {'update.json', 'update.json.sig'} <= assets.keys():
        raise RuntimeError('Этот релиз ещё не поддерживает обновление из приложения')
    raw = fetch(trusted_url(assets['update.json']), 512 * 1024)
    sig = base64.b64decode(fetch(trusted_url(assets['update.json.sig']), 4096), validate=True)
    key = serialization.load_pem_public_key(Path(__file__).with_name('update-public.pem').read_bytes())
    try:
        key.verify(sig,raw,padding.PKCS1v15(),hashes.SHA256())
    except Exception as exc:
        raise ValueError('Подпись обновления не подтверждена') from exc
    manifest = json.loads(raw)
    if manifest.get('schema') != 1 or release['tag_name'] != 'bundle-v' + manifest['bundle']:
        raise ValueError('Неверный формат обновления')
    component = 'windows' if sys.platform == 'win32' else 'arch'
    info = manifest['components'][component]
    version_tuple(info['version'])
    trusted_url(info['url'])
    if info['url'] not in assets.values() or not re.fullmatch('[a-f0-9]{64}',info['sha256']) or not 0 < info['size'] <= MAX_FILE:
        raise ValueError('Некорректные данные файла')
    if info.get('protocol') != 1:
        raise ValueError('Версия требует ручного обновления всех компонентов')
    return {'manifest': manifest, 'info': info, 'available':version_tuple(info['version']) > version_tuple(VERSION)}

def download(info, directory, progress, cancelled):
    directory.mkdir(parents=True,exist_ok=True)
    filename = Path(urlparse(trusted_url(info['url'])).path).name
    dest = directory / filename
    part = dest.with_suffix(dest.suffix + '.part')
    digest = hashlib.sha256()
    count = 0
    req = request.Request(info['url'],headers={'User-Agent':'RemoteVibecode-updater'})
    try:
        with request.urlopen(req,timeout=30) as response, part.open('wb') as out:
            if urlparse(response.url).scheme != 'https':
                raise ValueError('Небезопасное перенаправление')
            while chunk := response.read(65536):
                if cancelled.is_set():
                    raise RuntimeError('Скачивание отменено')
                count += len(chunk)
                if count > info['size']:
                    raise ValueError('Размер обновления не совпадает')
                out.write(chunk)
                digest.update(chunk)
                progress(int(count * 100 / info['size']))
        if count != info['size'] or digest.hexdigest() != info['sha256']:
            raise ValueError('Контрольная сумма обновления не совпадает')
        os.replace(part,dest)
        return dest
    finally:
        part.unlink(missing_ok=True)

def prepare_install(info, source, config_path):
    if hashlib.sha256(source.read_bytes()).hexdigest() != info['sha256']:
        raise ValueError('Файл изменился после проверки')
    directory = config_path.parent / 'updates'
    job = directory / 'install.json'
    ready = directory / 'ready'
    ready.unlink(missing_ok=True)
    data = {'pid': os.getpid(), 'source':str(source.resolve()), 'sha256':info['sha256'],
            'config':str(config_path.resolve()), 'ready':str(ready), 'platform':sys.platform}
    if sys.platform == 'win32':
        if not getattr(sys,'frozen',False):
            raise RuntimeError('Автообновление Windows доступно в установленном EXE')
        target = Path(sys.executable).resolve()
        # Prove the application directory is writable before closing the agent.
        probe = target.with_suffix('.update-probe')
        probe.write_bytes(b'')
        probe.unlink()
        data['target'] = str(target)
        helper = directory / 'RemoteVibecodeUpdater.exe'
        shutil.copy2(source,helper)
        command = [str(helper),'--apply-update',str(job)]
    else:
        if not Path('/usr/bin/pkexec').is_file():
            raise RuntimeError('Установите polkit: sudo pacman -S polkit')
        if not Path('/usr/bin/pacman').is_file() or not Path('/usr/bin/remotevibecode-agent').is_file():
            raise RuntimeError('Автообновление доступно для пакета Arch. Для исходников используйте git pull.')
        helper = directory / 'apply_update.py'
        shutil.copy2(Path(__file__),helper)
        command = [sys.executable,str(helper),'--apply-update',str(job)]
    job.write_text(json.dumps(data),encoding='utf-8')
    if sys.platform != 'win32':
        job.chmod(0o600)
    # Runs independently, even when the UI exits and closes its log stream.
    log = (directory / 'update.log').open('ab')
    try:
        subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                         creationflags=0x00000008 if sys.platform == 'win32' else 0,
                         start_new_session=sys.platform != 'win32')
    finally:
        log.close()

def process_alive(pid):
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000,False,pid)
        if not handle: return False
        try: return kernel.WaitForSingleObject(handle,0) == 0x102
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0); return True
    except ProcessLookupError: return False

def apply(job_path):
    data = json.loads(Path(job_path).read_text(encoding='utf-8'))
    source = Path(data['source'])
    for _ in range(120):
        if not process_alive(data['pid']): break
        time.sleep(.5)
    else: raise RuntimeError('Агент не завершился; установка отменена')
    outcome = Path(job_path).with_name('result.json')
    def result(success, message):
        try:
            outcome.write_text(json.dumps({'success':success,'message':message},ensure_ascii=False),encoding='utf-8')
        except OSError:
            print(message, flush=True)
    try:
        if hashlib.sha256(source.read_bytes()).hexdigest() != data['sha256']:
            raise ValueError('Файл изменился перед установкой')
    except Exception as exc:
        result(False, 'Установка отменена: ' + str(exc))
        command = [data['target']] if sys.platform == 'win32' else ['/usr/bin/remotevibecode-agent']
        subprocess.Popen(command + ['--config', data['config']], start_new_session=sys.platform != 'win32')
        return
    if sys.platform != 'win32':
        try:
            r = subprocess.run(['/usr/bin/pkexec','/usr/bin/pacman','-U','--noconfirm','--',str(source)],check=False)
            result(r.returncode == 0, 'Агент обновлён' if r.returncode == 0 else 'Установка отменена или не удалась. Прежний агент запущен снова.')
        except Exception as exc:
            result(False,str(exc))
        subprocess.Popen(['/usr/bin/remotevibecode-agent','--config',data['config']],start_new_session=True)
        return
    target = Path(data['target'])
    backup = target.with_suffix('.previous.exe')
    staged = target.with_suffix('.next.exe')
    child = None
    replaced = False
    try:
        shutil.copy2(source,staged)
        backup.unlink(missing_ok=True)
        os.replace(target,backup)
        replaced = True
        os.replace(staged,target)
        child = subprocess.Popen([str(target),'--config',data['config'],'--update-ready',data['ready']])
        for _ in range(120):
            if Path(data['ready']).is_file():
                result(True,'Агент обновлён. Прежний EXE сохранён для отката.')
                return
            if child.poll() is not None: break
            time.sleep(.5)
        raise RuntimeError('Новая версия не запустилась; выполнен откат')
    except Exception as exc:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=15)
        if replaced and backup.exists():
            target.unlink(missing_ok=True)
            os.replace(backup,target)
        result(False,str(exc))
        subprocess.Popen([str(target),'--config',data['config']])
    finally:
        staged.unlink(missing_ok=True)

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--apply-update':
        apply(sys.argv[2])
