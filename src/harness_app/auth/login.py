"""Browser OAuth callback, PKCE exchange and verified account registration."""

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from ..config import AUTH, PLAN_SCOPE, RESOURCE
from ..errors import HarnessError, check_http
from .session import save_credentials


def parse_callback(path, state, client_id=None):
    url = urlsplit(path)
    params = parse_qs(url.query, keep_blank_values=True)
    if url.path != '/auth/callback' or any(len(v) != 1 for v in params.values()):
        raise HarnessError('登入 callback 格式不正確。')
    if not secrets.compare_digest(params.get('state', [''])[0].encode(), state.encode()):
        raise HarnessError('登入 state 不符，請重新執行。')
    if 'error' in params:
        raise HarnessError('登入或方案額度授權被取消，請重新執行。')
    code = params.get('code', [''])[0]
    issued = params.get('client_id', [client_id])[0]
    if not code or not issued or issued == 'dynamic_agent_client':
        raise HarnessError('登入沒有回傳完整的授權碼與 client ID。')
    if client_id and issued != client_id:
        raise HarnessError('登入回傳了不同的 client ID。')
    return code, issued


def validate_tokens(tokens, client_id, nonce, jwks, subject=None):
    try:
        header = jwt.get_unverified_header(tokens['id_token'])
        keys = [key for key in jwks['keys'] if key.get('kid') == header.get('kid')]
        if len(keys) != 1:
            raise HarnessError('沒有唯一符合的簽章金鑰。')
        claims = jwt.decode(
            tokens['id_token'], jwt.PyJWK.from_dict(keys[0]).key,
            algorithms=['RS256'], audience=client_id, issuer=AUTH,
            options={'require': ['exp', 'iss', 'aud', 'sub', 'nonce']},
        )
        if not isinstance(claims['nonce'], str) or not secrets.compare_digest(claims['nonce'], nonce):
            raise HarnessError('Nonce 不符。')
        if not isinstance(claims['sub'], str) or not claims['sub']:
            raise HarnessError('缺少身分識別碼。')
        if subject and claims['sub'] != subject:
            raise HarnessError('帳號不符。')
        if not tokens.get('access_token') or tokens.get('token_type', '').lower() != 'bearer':
            raise HarnessError('缺少可用的 access token。')
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, AttributeError):
        raise HarnessError('無法驗證登入身分，請重新執行並選擇原本的帳號。') from None
    scope = tokens.get('scope', '')
    if not isinstance(scope, str) or PLAN_SCOPE not in scope.split():
        raise HarnessError('此登入未授權使用 ChatGPT 方案額度，無法呼叫模型。')
    return claims['sub']


def write_json(path, data):
    # Atomic replacement leaves the previous registration intact if writing fails.
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_registration(path, client_id, subject=None):
    registration = {'client_id': client_id}
    if subject:
        registration['subject'] = subject
    write_json(path, registration)


def read_json(path):
    if path.is_symlink():
        raise HarnessError('本機註冊資料不可使用符號連結。')
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise HarnessError('本機註冊資料格式錯誤。')
    return data


class LoopbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


def login(client, directory):
    host_path = directory / 'host.json'
    host = read_json(host_path)
    if not host:
        host = {'ext_agent_host_id': 'urn:uuid:' + str(uuid.uuid4())}
        write_json(host_path, host)
    profile_path = directory / 'registration.json'
    profile = read_json(profile_path)
    saved_id = profile.get('client_id')
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    outcome = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback query strings contain the authorization code.

        def do_GET(self):
            if urlsplit(self.path).path != '/auth/callback':
                self.send_error(404)
                return
            try:
                outcome.append(parse_callback(self.path, state, saved_id))
                status, message = 200, 'Callback received. Return to your terminal to finish verification.'
            except ValueError as error:
                outcome.append(error)
                status, message = 400, 'Sign-in failed. Return to your terminal.'
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(message.encode())

    with LoopbackServer(('127.0.0.1', 0), Callback) as server:
        redirect = 'http://127.0.0.1:' + str(server.server_port) + '/auth/callback'
        params = {
            'client_id': saved_id or 'dynamic_agent_client',
            'ext_agent_host_id': host['ext_agent_host_id'],
            'response_type': 'code', 'redirect_uri': redirect,
            'scope': 'openid profile email offline_access resource.invoke ' + PLAN_SCOPE,
            'resource': RESOURCE, 'state': state, 'nonce': nonce,
            'code_challenge_method': 'S256', 'code_challenge': challenge,
        }
        if not saved_id:
            params['agent_name_hint'] = 'Python Harness Lesson'
        url = AUTH + '/api/accounts/authorize?' + urlencode(params)
        print('Continue with ChatGPT：請在瀏覽器登入並授權使用方案額度。', flush=True)
        if not webbrowser.open(url):
            print('瀏覽器未自動開啟，請在本機開啟此連結：\n' + url, flush=True)
        deadline = time.monotonic() + 300
        server.timeout = 1
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    if not outcome:
        raise HarnessError('登入等待逾時（五分鐘），請重新執行。')
    if isinstance(outcome[0], ValueError):
        raise outcome[0]
    code, issued = outcome[0]
    save_registration(profile_path, issued, profile.get('subject'))
    response = client.post(AUTH + '/api/accounts/oauth/token', data={
        'grant_type': 'authorization_code', 'client_id': issued, 'code': code,
        'code_verifier': verifier, 'redirect_uri': redirect, 'resource': RESOURCE,
    })
    check_http(response)
    tokens = response.json()
    response = client.get(AUTH + '/.well-known/jwks.json')
    check_http(response)
    subject = validate_tokens(tokens, issued, nonce, response.json(), profile.get('subject'))
    save_registration(profile_path, issued, subject)
    save_credentials(directory, issued, subject, host['ext_agent_host_id'], tokens)
    return tokens['access_token']
