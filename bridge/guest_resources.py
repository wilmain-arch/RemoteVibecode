"""Explicit ACL reads and independent work copies; never mount an owner's root."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import uuid
from .guest_access import AccessError
from .workspace_listing import listing

MAX_COPY=100*1024*1024
MAX_GUEST_STORAGE=256*1024*1024
MAX_GUEST_ENTRIES=10000


EXCLUDED_DIRS={'.git','.codex','.agents','.ssh','.aws','node_modules','build','__pycache__'}
def shareable_path(relative):
    parts=Path(relative).parts
    return not any(part in EXCLUDED_DIRS for part in parts) and not any(
        part.startswith('.env') or part=='auth.json' or part.endswith(('.pem','.key')) for part in parts)


def safe_path(root,relative):
    if not isinstance(relative,str) or len(relative)>1024 or Path(relative).is_absolute():raise ValueError('Некорректный путь')
    root=Path(root)
    if root.is_symlink():raise ValueError('Корневая папка не должна быть ссылкой')
    root=root.resolve()
    parts=Path(relative).parts
    if any(part in ('.','..') for part in parts):raise ValueError('Путь вне проекта')
    path=root
    for part in parts:
        path/=part
        if path.is_symlink():raise ValueError('Ссылки в гостевых ресурсах недоступны')
    if not path.resolve().is_relative_to(root):raise ValueError('Путь вне проекта')
    return path


def read_regular(root,relative,limit=MAX_COPY,missing=False,prefix=False):
    """Open components relative to directory descriptors; reject symlink races."""
    path=safe_path(root,relative)
    parts=path.relative_to(Path(root).resolve()).parts
    if not parts:raise ValueError('Выберите файл')
    descriptors=[]
    try:
        fd=os.open(Path(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in parts[:-1]:
            try:fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                if missing:return None
                raise ValueError('Файл не найден') from None
            descriptors.append(fd)
        try:leaf=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:
            if missing:return None
            raise ValueError('Файл не найден') from None
        descriptors.append(leaf)
        info=os.fstat(leaf)
        if not stat.S_ISREG(info.st_mode) or (info.st_size>limit and not prefix):raise ValueError('Файл недоступен или слишком большой')
        chunks=[];size=0
        while True:
            chunk=os.read(leaf,min(65536,limit+1-size))
            if not chunk:break
            size+=len(chunk)
            if size>limit and not prefix:raise ValueError('Файл слишком большой')
            chunks.append(chunk)
            if prefix and size>limit:break
        return b''.join(chunks)
    finally:
        for fd in reversed(descriptors):os.close(fd)


def file_hash(root,relative):
    data=read_regular(root,relative,missing=True)
    return None if data is None else hashlib.sha256(data).hexdigest()


class GuestResources:
    def __init__(self,bridge):
        self.bridge=bridge
        self.storage_lock=threading.RLock()
        self.review_lock=threading.RLock()
        self.access=bridge.guests
        with self.access.lock:
            self.access.db.execute('CREATE TABLE IF NOT EXISTS guest_copies(id TEXT PRIMARY KEY,guest TEXT NOT NULL REFERENCES guests(id),kind TEXT,resource TEXT,source TEXT,manifest TEXT)')

    def right(self,guest,kind,resource,work=False):
        if kind not in ('thread','project') or not isinstance(resource,str) or not 1<=len(resource)<=512:
            raise ValueError('Некорректный ресурс')
        with self.access.lock:
            self.access._guest(guest)
            row=self.access.db.execute('SELECT right FROM grants WHERE guest=? AND kind=? AND resource=?',(guest,kind,resource)).fetchone()
            if not row or work and row[0]!='work':raise AccessError('Ресурс не предоставлен гостю')
            return row[0]

    def root(self,kind,resource):
        if kind=='thread':return self.bridge.workspace_path(resource)[0]
        if kind=='project':
            project=next((p for p in self.bridge.projects()['projects'] if p['id']==resource),None)
            if not project or not project.get('cwd'):raise ValueError('Проект не найден или не имеет папки')
            path=Path(project['cwd'])
            if not path.is_dir():raise ValueError('Папка проекта недоступна')
            return path.resolve()
        raise ValueError('Некорректный ресурс')

    def shared(self,guest):
        grants=self.access.view(guest)['grants']
        projects=self.bridge.projects()['projects'] if grants else []
        for grant in grants:
            if grant['kind']=='project':
                project=next((p for p in projects if p['id']==grant['resourceId']),None)
                if project:
                    grant['name']=project.get('name',grant['resourceId'])
                    grant['threads']=[{'id':t['id'],'name':t.get('title') or t.get('name') or t['id']} for t in project.get('threads',[])]
            else:
                thread=next((t for p in projects for t in p.get('threads',[]) if t['id']==grant['resourceId']),None)
                if thread:grant['name']=thread.get('title') or thread.get('name') or thread['id']
        with self.access.lock:
            self.access._guest(guest)
            for grant in grants:self.right(guest,grant['kind'],grant['resourceId'])
            copies=[{'id':i,'kind':k,'resourceId':r} for i,k,r in self.access.db.execute('SELECT id,kind,resource FROM guest_copies WHERE guest=?',(guest,))]
        return {'resources':grants,'copies':copies}

    def read(self,guest,kind,resource,relative='',preview=False,blob=False):
        self.right(guest,kind,resource)
        root=self.root(kind,resource)
        if isinstance(relative,str) and Path(relative).is_absolute():
            try:relative=Path(relative).relative_to(root).as_posix()
            except ValueError:raise AccessError('Ссылка находится вне предоставленного проекта') from None
        if not shareable_path(relative):raise AccessError('Служебные папки и ключи не предоставляются гостям')
        root=self.root(kind,resource);path=safe_path(root,relative)
        if blob:
            import base64,mimetypes
            data=read_regular(root,relative,8*1024*1024)
            self.right(guest,kind,resource)
            return {'name':path.name,'mime':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','dataBase64':base64.b64encode(data).decode(),'path':relative}
        if not preview:
            result=listing(root,path,relative)
            result['entries']=[item for item in result.get('entries',[]) if shareable_path(item['path'])]
            self.right(guest,kind,resource)
            return result
        if not path.is_file():raise ValueError('Файл не найден')
        data=read_regular(root,relative,limit=131072,prefix=True)
        self.right(guest,kind,resource)
        truncated=len(data)>131072
        import codecs
        try:
            if b'\0' in data:return {'previewable':False,'reason':'Двоичный файл'}
            text=codecs.getincrementaldecoder('utf-8')().decode(data[:131072],final=not truncated)
        except UnicodeDecodeError:return {'previewable':False,'reason':'Двоичный файл'}
        return {'previewable':True,'text':text,'path':relative,'truncated':truncated}

    def history(self,guest,thread,before=None):
        def authorize():
            try:self.right(guest,'thread',thread);return
            except AccessError:
                for grant in self.access.view(guest)['grants']:
                    if grant['kind']=='project':
                        project=next((p for p in self.bridge.projects()['projects'] if p['id']==grant['resourceId']),None)
                        if project and any(t['id']==thread for t in project['threads']):
                            self.right(guest,'project',grant['resourceId']);return
                raise AccessError('Чат не предоставлен гостю') from None
        authorize()
        result=self.bridge.history(thread,before)
        authorize()
        return result

    def prepare_copy(self,guest,data):
        with self.storage_lock:
            return self._prepare_copy(guest,data)

    def _prepare_copy(self,guest,data):
        kind=data.get('kind');resource=data.get('resourceId');op=data.get('operationId')
        if not isinstance(op,str) or not 8<=len(op)<=100:raise ValueError('Нужен operationId')
        self.right(guest,kind,resource,True)
        copy_id=hashlib.sha256((guest+'\0'+op).encode()).hexdigest()[:32]
        with self.access.lock:
            old=self.access.db.execute('SELECT kind,resource FROM guest_copies WHERE id=?',(copy_id,)).fetchone()
        if old:
            if old!=(kind,resource):raise ValueError('ID уже использован для другого ресурса')
            return {'scopeId':copy_id}
        if self.storage_status(guest)['exhausted']:raise ValueError('Гостевое хранилище заполнено')
        source=self.root(kind,resource)
        base=self.bridge.state_file.parent/'guest-runtime'/guest/'copies'
        base.mkdir(parents=True,mode=0o700,exist_ok=True)
        if any(p.is_symlink() for p in [base,*base.parents]):raise ValueError('Некорректная папка копии')
        destination=base/copy_id
        staging=base/('pending-'+uuid.uuid4().hex)
        staging.mkdir(mode=0o700)
        manifest={};total=0
        try:
            for folder,dirs,files in os.walk(source,followlinks=False):
                dirs[:]=[d for d in dirs if shareable_path((Path(folder)/d).relative_to(source)) and not (Path(folder)/d).is_symlink()]
                for name in files:
                    if name.startswith('.env') or name in ('auth.json',) or name.endswith(('.pem','.key')):continue
                    path=Path(folder)/name
                    if path.is_symlink() or not path.is_file():continue
                    relative=path.relative_to(source).as_posix()
                    if not shareable_path(relative):continue
                    content=read_regular(source,relative,limit=MAX_COPY)
                    if content is None:raise ValueError('Файл слишком большой для рабочей копии')
                    total+=len(content)
                    if total>MAX_COPY or len(manifest)>=10000:raise ValueError('Копия ограничена 100 МиБ и 10000 файлами')
                    target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(content)
                    manifest[relative]=hashlib.sha256(content).hexdigest()
            self.right(guest,kind,resource,True)
            def save():
                self.right(guest,kind,resource,True)
                old=self.access.db.execute('SELECT kind,resource FROM guest_copies WHERE id=?',(copy_id,)).fetchone()
                if old:
                    if old!=(kind,resource):raise ValueError('ID уже использован для другого ресурса')
                    return
                if self.storage_status(guest)['exhausted']:raise ValueError('Гостевое хранилище заполнено')
                if destination.exists():raise ValueError('Подготовка копии уже выполняется; обновите список')
                os.rename(staging,destination)
                self.access.db.execute('INSERT INTO guest_copies VALUES(?,?,?,?,?,?)',(copy_id,guest,kind,resource,str(source),json.dumps(manifest)))
            self.access._transaction(save)
            return {'scopeId':copy_id,'files':len(manifest),'bytes':total,'excludedSecrets':True}
        finally:
            if staging.exists():shutil.rmtree(staging)

    def copy_workspace(self,guest,scope):
        with self.access.lock:
            row=self.access.db.execute('SELECT kind,resource FROM guest_copies WHERE id=? AND guest=?',(scope,guest)).fetchone()
        if not row:raise AccessError('Рабочая копия недоступна')
        self.right(guest,*row,work=True)
        return self.bridge.state_file.parent/'guest-runtime'/guest/'copies'/scope

    def owner_copies(self,guest,before=None):
        with self.access.lock:
            self.access._guest(guest)
            point=None
            if before:
                point=self.access.db.execute('SELECT rowid FROM guest_copies WHERE guest=? AND id=?',(guest,before)).fetchone()
                if not point:raise ValueError('Недействительная граница списка копий')
            rows=self.access.db.execute('SELECT id,kind,resource FROM guest_copies WHERE guest=? AND rowid<? ORDER BY rowid DESC LIMIT 101',
                (guest,point[0] if point else 9223372036854775807)).fetchall()
            return {'copies':[{'id':i,'kind':k,'resourceId':r} for i,k,r in rows[:100]],
                'nextCursor':rows[99][0] if len(rows)>100 else None}

    def review(self,scope,relative=None,confirmed=False,expected=None):
        with self.review_lock:
            return self._review(scope,relative,confirmed,expected)

    def _review(self,scope,relative=None,confirmed=False,expected=None):
        with self.access.lock:
            row=self.access.db.execute('SELECT guest,source,manifest FROM guest_copies WHERE id=?',(scope,)).fetchone()
        if not row:raise ValueError('Копия не найдена')
        guest,source,raw=row;manifest=json.loads(raw)
        copy=self.bridge.state_file.parent/'guest-runtime'/guest/'copies'/scope
        if relative is None:
            files=set(manifest)
            for folder,dirs,names in os.walk(copy,followlinks=False):
                dirs[:]=[d for d in dirs if d!='.git' and not (Path(folder)/d).is_symlink()]
                files.update((Path(folder)/n).relative_to(copy).as_posix() for n in names)
                if len(files)>10000:raise ValueError('Слишком много файлов для просмотра')
            changes=[]
            for path in sorted(files):
                try:
                    current=file_hash(copy,path);owner=file_hash(Path(source),path)
                    if current!=manifest.get(path):changes.append({'path':path,'baseHash':manifest.get(path),'guestHash':current,'ownerHash':owner,'conflict':owner!=manifest.get(path)})
                except ValueError:changes.append({'path':path,'blocked':True})
            return {'changes':changes,'scopeId':scope}
        original=safe_path(Path(source),relative);result=safe_path(copy,relative)
        owner_hash=file_hash(Path(source),relative);guest_data=read_regular(copy,relative,missing=True);guest_hash=None if guest_data is None else hashlib.sha256(guest_data).hexdigest()
        if confirmed:
            if guest_hash is None:raise ValueError('Удаление файлов из APK не применяется; проверьте на ПК')
            if owner_hash!=manifest.get(relative) or expected!=guest_hash:raise ValueError('Файл изменился. Обновите просмотр изменений')
            if not result.is_file():raise ValueError('Нет результата гостя')
            fds=[];temporary='rv-review-'+uuid.uuid4().hex
            try:
                fd=os.open(Path(source),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
                for part in original.relative_to(Path(source).resolve()).parts[:-1]:
                    try:os.mkdir(part,mode=0o755,dir_fd=fd)
                    except FileExistsError:pass
                    fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd)
                leaf=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                with os.fdopen(leaf,'wb') as stream:stream.write(guest_data)
                # Check the actual destination descriptor, not a re-resolved path.
                try:
                    leaf=os.open(original.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                except FileNotFoundError:current_hash=None
                else:
                    with os.fdopen(leaf,'rb') as stream:
                        info=os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_COPY:raise ValueError('Файл владельца недоступен')
                        current_hash=hashlib.sha256(stream.read(MAX_COPY+1)).hexdigest()
                    os.chmod(temporary,stat.S_IMODE(info.st_mode),dir_fd=fd,follow_symlinks=False)
                if current_hash!=owner_hash:raise ValueError('Файл владельца изменился')
                os.replace(temporary,original.name,src_dir_fd=fd,dst_dir_fd=fd)
            finally:
                if fds:
                    try:os.unlink(temporary,dir_fd=fds[-1])
                    except FileNotFoundError:pass
                for fd in reversed(fds):os.close(fd)
            manifest[relative]=guest_hash
            self.access._transaction(lambda:self.access.db.execute('UPDATE guest_copies SET manifest=? WHERE id=?',(json.dumps(manifest),scope)))
            return {'applied':True,'path':relative}
        import difflib
        if (original.exists() and original.stat().st_size>131072) or (result.exists() and result.stat().st_size>131072):raise ValueError('Файл слишком большой для текстового diff')
        try:
            old_data=read_regular(Path(source),relative,131072,missing=True)
            old=old_data.decode('utf-8') if old_data is not None else ''
            new=guest_data.decode('utf-8') if guest_data is not None else ''
        except UnicodeDecodeError:raise ValueError('Двоичный файл; проверьте его на ПК') from None
        return {'diff':''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='owner/'+relative,tofile='guest/'+relative)), 'guestHash':guest_hash}

    def storage_status(self,guest,exclude=()):
        """Bounded scan of guest data only; symlinks and broker auth are excluded.

        This is a monitored budget, not a filesystem hard quota. The executor
        also has a kernel-enforced per-file size limit.
        """
        self.access._guest(guest)
        base=self.bridge.state_file.parent/'guest-runtime'/guest
        total=0;entries=0
        excluded=set(exclude)
        for name in ('workspace','copies'):
            root=base/name
            if not root.exists():continue
            if root.is_symlink():raise ValueError('Некорректная гостевая папка')
            pending=[root]
            while pending:
                directory=pending.pop()
                fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    with os.scandir(fd) as scan:
                        for entry in scan:
                            if directory/entry.name in excluded:continue
                            entries+=1
                            info=entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(info.st_mode):pending.append(directory/entry.name)
                            elif stat.S_ISREG(info.st_mode):total+=info.st_size
                            if total>MAX_GUEST_STORAGE or entries>MAX_GUEST_ENTRIES:
                                return {'exhausted':True,'bytes':total,'entries':entries}
                finally:os.close(fd)
        return {'exhausted':False,'bytes':total,'entries':entries}

    def own_root(self,guest,scope='default'):
        if not isinstance(scope,str) or not 1<=len(scope)<=80:raise ValueError('Некорректная рабочая область')
        self.access._guest(guest)
        if scope!='default':return self.copy_workspace(guest,scope)
        root=self.bridge.state_file.parent/'guest-runtime'/guest/'workspace'
        root.mkdir(parents=True,mode=0o700,exist_ok=True)
        if any(p.is_symlink() for p in [root,*root.parents]):raise ValueError('Некорректная гостевая папка')
        root.chmod(0o700)
        return root

    def own_read(self,guest,scope,relative,blob=False):
        root=self.own_root(guest,scope)
        path=safe_path(root,relative)
        if not blob:
            result=self.own_listing(root,relative)
            self.own_root(guest,scope)
            return result
        import base64,mimetypes
        data=read_regular(root,relative,8*1024*1024)
        self.own_root(guest,scope)
        return {'name':path.name,'mime':mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            'dataBase64':base64.b64encode(data).decode(),'path':relative}

    def own_write(self,guest,data):
        with self.storage_lock:
            return self._own_write(guest,data)

    def _own_write(self,guest,data):
        import base64,binascii
        root=self.own_root(guest,data.get('scopeId','default'))
        relative=data.get('path')
        if not relative:raise ValueError('Нужно имя файла')
        path=safe_path(root,relative)
        if not isinstance(data.get('dataBase64'),str):raise ValueError('Нужны данные файла')
        try:content=base64.b64decode(data['dataBase64'],validate=True)
        except (ValueError,binascii.Error):raise ValueError('Некорректные данные файла') from None
        if len(content)>4*1024*1024:raise ValueError('Загрузка ограничена 4 МиБ')
        storage=self.storage_status(guest,exclude=(path,))
        if storage['exhausted'] or storage['bytes']+len(content)>MAX_GUEST_STORAGE or storage['entries']+1>MAX_GUEST_ENTRIES:
            raise ValueError('Гостевое хранилище заполнено; освободите место на ПК')
        # Traverse parents via fds, so a concurrent guest cannot redirect a write.
        fds=[];temporary='rv-upload-'+uuid.uuid4().hex
        try:
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
            for part in path.relative_to(root.resolve()).parts[:-1]:
                fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd)
            leaf=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(leaf,'wb') as stream:stream.write(content)
            with self.access.lock:
                self.own_root(guest,data.get('scopeId','default'))
                storage=self.storage_status(guest,exclude=(path,path.parent/temporary))
                if storage['exhausted'] or storage['bytes']+len(content)>MAX_GUEST_STORAGE or storage['entries']+1>MAX_GUEST_ENTRIES:
                    raise ValueError('Гостевое хранилище заполнено')
                os.rename(temporary,path.name,src_dir_fd=fd,dst_dir_fd=fd)
            return {'uploaded':True,'path':relative,'size':len(content)}
        finally:
            if fds:
                try:os.unlink(temporary,dir_fd=fds[-1])
                except FileNotFoundError:pass
            for fd in reversed(fds):os.close(fd)

    def own_listing(self,root,relative):
        path=safe_path(root,relative)
        fds=[]
        try:
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
            for part in path.relative_to(root.resolve()).parts:
                fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd)
            entries=[]
            with os.scandir(fd) as scan:
                for entry in scan:
                    if len(entries)>=2000:raise ValueError('В папке слишком много файлов. Откройте вложенную папку на ПК')
                    info=entry.stat(follow_symlinks=False)
                    is_dir=stat.S_ISDIR(info.st_mode);is_file=stat.S_ISREG(info.st_mode)
                    if not is_dir and not is_file:continue
                    entries.append({'name':entry.name,'path':'/'.join(filter(None,[relative,entry.name])),
                        'isDirectory':is_dir,'available':True,'size':0 if is_dir else info.st_size})
            entries.sort(key=lambda e:(not e['isDirectory'],e['name'].casefold()))
            return {'path':relative,'entries':entries,'total':len(entries)}
        finally:
            for fd in reversed(fds):os.close(fd)
