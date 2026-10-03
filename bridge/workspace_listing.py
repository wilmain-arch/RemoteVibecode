"""Read-only, snapshot-bound directory pagination."""
import base64
import binascii
import hashlib
import json

SERVICE_DIRS = {'build', 'node_modules', '__pycache__'}


def listing(root, directory, relative, query='', hidden=False, service=False, cursor=None):
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('Слишком длинный запрос поиска')
    entries = []
    for count, path in enumerate(directory.iterdir()):
        if count >= 50000:
            raise ValueError('В папке больше 50 000 элементов. Откройте вложенную папку на ПК')
        if not hidden and path.name.startswith('.') or not service and path.name in SERVICE_DIRS:
            continue
        if query.casefold() not in path.name.casefold():
            continue
        try:
            target = path.resolve()
            blocked = not target.is_relative_to(root) or not target.exists()
            if not blocked and not target.is_file() and not target.is_dir():
                continue
            entries.append({'name': path.name, 'path': '/'.join(filter(None, [relative, path.name])),
                'isDirectory': not blocked and target.is_dir(), 'size': 0 if blocked or target.is_dir() else target.stat().st_size,
                'isLink': path.is_symlink(), 'available': not blocked,
                'blockedReason': 'Ссылка ведёт вне проекта или её цель недоступна' if blocked else ''})
        except (OSError, RuntimeError):
            continue
    entries.sort(key=lambda x: (not x['isDirectory'], x['name'].casefold(), x['name']))
    digest = hashlib.sha256(json.dumps([str(root), relative, query, hidden, service, entries], sort_keys=True).encode()).hexdigest()
    offset = 0
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError("Некорректный курсор")
            state = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            offset = state['offset']
            if state['snapshot'] != digest:
                raise ValueError('Список папки изменился. Обновите список')
            if type(offset) is not int or offset < 0 or offset > len(entries):
                raise ValueError('Некорректный курсор')
        except (KeyError, TypeError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError('Некорректный курсор') from exc
    next_offset = offset + 200
    next_cursor = base64.urlsafe_b64encode(json.dumps({'offset': next_offset, 'snapshot': digest}).encode()).decode() if next_offset < len(entries) else None
    return {'rootName': root.name, 'path': relative, 'entries': entries[offset:next_offset],
            'total': len(entries), 'nextCursor': next_cursor, 'truncated': next_cursor is not None}
