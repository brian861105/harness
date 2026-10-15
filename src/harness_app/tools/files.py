"""Project file policy, bounded reads/search and approved exact-text edits."""

import difflib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from ..errors import HarnessError
from .limits import FILE_LIMIT, OUTPUT_LIMIT, PRIVATE_PARTS
from .shell import run_process


class FileTools:
    def __init__(self, workspace, approve=None, notify=None):
        self.workspace = Path(workspace).resolve()
        self.approve = approve or (lambda name, preview: False)
        self.notify = notify or (lambda name, detail: None)
        self.side_effects = False

    def path(self, value):
        if not isinstance(value, str) or not value or '\0' in value:
            raise HarnessError('path 必須是專案內的相對路徑。')
        relative = Path(value)
        if relative.is_absolute() or any(part == '..' or part.casefold() in PRIVATE_PARTS
                                          or part.casefold().startswith('.env') for part in relative.parts):
            raise HarnessError('檔案工具不可存取專案外、私人或內部路徑。')
        target = self.workspace
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                raise HarnessError('檔案工具不可跟隨符號連結。')
        return target

    def read(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise HarnessError('只能讀寫一般檔案。')
            content = file.read(FILE_LIMIT + 1)
        if len(content) > FILE_LIMIT:
            raise HarnessError('檔案超過 1 MiB，請改用 grep 或經確認的命令。')
        return content.decode('utf-8'), stat.S_IMODE(info.st_mode)

    def read_file(self, path, start_line=1, end_line=200):
        if (type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or not start_line <= end_line < start_line + 200):
            raise HarnessError('讀取範圍必須是正整數，且不超過 200 行。')
        target = self.path(path)
        self.notify('read_file', path)
        text, _ = self.read(target)
        lines = text.splitlines(keepends=True)
        content = ''.join('{}: {}'.format(number, line)
                          for number, line in enumerate(lines[start_line-1:end_line], start_line)).encode('utf-8')
        return {'ok': True, 'content': content[:OUTPUT_LIMIT].decode('utf-8', errors='ignore'),
                'total_lines': len(lines), 'truncated': len(content) > OUTPUT_LIMIT}

    def grep(self, pattern, path):
        if not isinstance(pattern, str) or '\0' in pattern or len(pattern) > 4096:
            raise HarnessError('搜尋 pattern 必須是有效字串，且不超過 4096 字元。')
        target = self.path(path)
        executable = shutil.which('rg')
        if executable is None:
            raise HarnessError('找不到 ripgrep；請先安裝 rg（macOS 可用 brew install ripgrep）。')
        args = [executable, '--no-config', '--line-number', '--with-filename', '--hidden', '--color', 'never']
        for part in sorted(PRIVATE_PARTS) + ['.env*']:
            args.extend(['--iglob', '!**/' + part, '--iglob', '!**/' + part + '/**'])
        args.extend(['--', pattern, str(target.relative_to(self.workspace))])
        self.notify('grep', pattern + ' · ' + path)
        result = run_process(args, self.workspace)
        if result['exit_code'] == 1 and result['reason'] is None:
            result['ok'] = True
        return result

    def apply_patch(self, path, old_text, new_text):
        target = self.path(path)
        if (old_text is not None and not isinstance(old_text, str)
                or new_text is not None and not isinstance(new_text, str)
                or old_text is None and new_text is None):
            raise HarnessError('old_text 與 new_text 必須是字串或 null。')
        exists = target.exists()
        before, mode = self.read(target) if exists else ('', 0o644)
        if old_text is None:
            if exists:
                raise HarnessError('建立檔案失敗：目標已存在。')
            after = new_text
        elif not exists:
            raise HarnessError('要修改的檔案不存在。')
        elif new_text is None:
            if old_text != before:
                raise HarnessError('刪除前必須提供完整且相符的 old_text。')
            after = ''
        else:
            if not old_text or before.count(old_text) != 1:
                raise HarnessError('old_text 必須在檔案中精確且只出現一次。')
            after = before.replace(old_text, new_text, 1)
        if len(after.encode('utf-8')) > FILE_LIMIT:
            raise HarnessError('修改後的檔案不可超過 1 MiB。')
        def diff_lines(text):
            return [line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                    for line in text.splitlines(keepends=True)]
        preview = ''.join(difflib.unified_diff(
            diff_lines(before), diff_lines(after),
            fromfile=path if exists else '/dev/null', tofile=path if new_text is not None else '/dev/null'))
        operation = 'create' if old_text is None else 'delete' if new_text is None else 'update'
        if not self.approve('apply_patch', operation + ' ' + path + '\n' + preview):
            return {'ok': False, 'error': '使用者拒絕修改；請勿重複提出相同操作。'}
        target = self.path(path)
        current = self.read(target)[0] if target.exists() else None
        if current != (before if exists else None):
            raise HarnessError('確認期間檔案已變更，請重新讀取後再提出修改。')
        self.side_effects = True
        if new_text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8', newline='') as file:
                    file.write(after)
                    file.flush()
                    os.fsync(file.fileno())
                    os.fchmod(file.fileno(), mode)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {'ok': True, 'path': path}
