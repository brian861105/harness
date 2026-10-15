"""Protected OAuth credentials and access-token renewal."""

import json
import math
import os
import re
import stat
import tempfile
import time

from ..config import AUTH, PLAN_SCOPE, RESOURCE
from ..errors import HarnessError

TERMINAL_REFRESH_ERRORS = {
    'invalid_grant', 'invalid_refresh_token', 'token_expired',
    'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused',
}


def _validate_record(record):
    fields = ('client_id', 'subject', 'ext_agent_host_id', 'access_token', 'refresh_token', 'scope')
    if (not isinstance(record, dict)
            or any(not isinstance(record.get(key), str) or not record[key] for key in fields)
            or record['client_id'] == 'dynamic_agent_client'
            or PLAN_SCOPE not in record['scope'].split()
            or isinstance(record.get('expires_at'), bool)
            or not isinstance(record.get('expires_at'), (int, float))
            or not math.isfinite(record['expires_at'])):
        raise HarnessError('登入快取格式不完整，請移除本程式的 credentials.json 後重新登入。')


def _read_json(path, protected=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise HarnessError('無法安全讀取本機登入資料；請檢查檔案與權限。') from None
    with os.fdopen(fd, 'r', encoding='utf-8') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise HarnessError('本機登入資料必須是一般檔案。')
        if protected and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise HarnessError('credentials.json 必須由目前使用者擁有，且權限設為 600。')
        try:
            value = json.load(file)
        except (ValueError, UnicodeError):
            raise HarnessError('本機登入資料已損壞，請移除本程式的 credentials.json 後重新登入。') from None
    if not isinstance(value, dict):
        raise HarnessError('本機登入資料格式不正確。')
    return value


def save_credentials(directory, client_id, subject, host_id, tokens):
    expires_in = tokens.get('expires_in')
    if (isinstance(expires_in, bool) or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in) or expires_in <= 0
            or not isinstance(tokens.get('token_type'), str)
            or tokens['token_type'].lower() != 'bearer'):
        raise HarnessError('授權伺服器沒有回傳有效的 token 到期資訊。')
    record = {
        'client_id': client_id, 'subject': subject, 'ext_agent_host_id': host_id,
        'access_token': tokens.get('access_token'), 'refresh_token': tokens.get('refresh_token'),
        'scope': tokens.get('scope'), 'expires_at': time.time() + expires_in,
    }
    _validate_record(record)
    path = directory / 'credentials.json'
    if path.is_symlink():
        raise HarnessError('credentials.json 不可使用符號連結。')
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AuthSession:
    def __init__(self, client, directory, browser_login):
        self.client = client
        self.directory = directory
        self.browser_login = browser_login

    def _saved(self):
        record = _read_json(self.directory / 'credentials.json', protected=True)
        if record is None:
            return None
        _validate_record(record)
        profile = _read_json(self.directory / 'registration.json') or {}
        host = _read_json(self.directory / 'host.json') or {}
        if (record['client_id'] != profile.get('client_id')
                or record['subject'] != profile.get('subject')
                or record['ext_agent_host_id'] != host.get('ext_agent_host_id')):
            raise HarnessError('登入快取與目前帳號或主機不符，請移除 credentials.json 後重新登入。')
        return record

    def _browser(self):
        self.browser_login(self.client, self.directory)
        record = self._saved()
        if record is None:
            raise HarnessError('登入完成後沒有可重用的授權資料。')
        return record['access_token']

    def access_token(self):
        record = self._saved()
        if record is None:
            return self._browser()
        if record['expires_at'] > time.time() + 60:
            return record['access_token']
        response = self.client.post(AUTH + '/api/accounts/oauth/token', data={
            'grant_type': 'refresh_token', 'client_id': record['client_id'],
            'refresh_token': record['refresh_token'], 'resource': RESOURCE,
        })
        if not response.is_success:
            code = None
            try:
                error = response.json().get('error')
                code = error.get('code') if isinstance(error, dict) else error
            except (ValueError, AttributeError):
                pass
            if response.status_code in (400, 401) and isinstance(code, str) and code in TERMINAL_REFRESH_ERRORS:
                (self.directory / 'credentials.json').unlink()
                return self._browser()
            safe_code = (' / ' + code if isinstance(code, str)
                         and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code) else '')
            raise HarnessError('更新登入失敗（HTTP {}{}），原登入資料已保留。'.format(
                response.status_code, safe_code))
        try:
            tokens = response.json()
        except ValueError:
            raise HarnessError('更新登入回應格式不正確，原登入資料已保留。') from None
        if not isinstance(tokens, dict):
            raise HarnessError('更新登入回應格式不正確，原登入資料已保留。')
        tokens.setdefault('scope', record['scope'])
        save_credentials(self.directory, record['client_id'], record['subject'],
                         record['ext_agent_host_id'], tokens)
        return tokens['access_token']
