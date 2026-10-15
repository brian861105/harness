import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from harness_app.auth import login as auth_login


class CallbackTests(unittest.TestCase):
    def test_new_registration_returns_issued_id_for_code_exchange(self):
        self.assertEqual(
            auth_login.parse_callback(
                '/auth/callback?state=expected&code=code&client_id=oaiapp_issued',
                'expected',
            ),
            ('code', 'oaiapp_issued'),
        )

    def test_reauthorization_accepts_omitted_client_id(self):
        self.assertEqual(
            auth_login.parse_callback('/auth/callback?state=s&code=c', 's', 'oaiapp_saved'),
            ('c', 'oaiapp_saved'),
        )

    def test_invalid_callbacks_cannot_produce_an_exchange_code(self):
        cases = [
            ('/other?state=s&code=c&client_id=oaiapp_new', None),
            ('/auth/callback?state=wrong&code=c&client_id=oaiapp_new', None),
            ('/auth/callback?state=%C3%A9&code=c&client_id=oaiapp_new', None),
            ('/auth/callback?state=s&state=s&code=c&client_id=oaiapp_new', None),
            ('/auth/callback?state=s&error=access_denied', None),
            ('/auth/callback?state=s&client_id=oaiapp_new', None),
            ('/auth/callback?state=s&code=c', None),
            ('/auth/callback?state=s&code=c&client_id=dynamic_agent_client', None),
            ('/auth/callback?state=s&code=c&client_id=oaiapp_other', 'oaiapp_saved'),
        ]
        for path, saved in cases:
            with self.subTest(path=path), self.assertRaises(ValueError):
                auth_login.parse_callback(path, 's', saved)


class TokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        public['kid'] = 'test-key'
        cls.jwks = {'keys': [public]}

    def tokens(self, **overrides):
        claims = {
            'iss': 'https://auth.openai.com', 'aud': 'oaiapp_test',
            'sub': 'account-a', 'exp': int(time.time()) + 300, 'nonce': 'nonce',
        }
        claims.update(overrides)
        return {
            'id_token': jwt.encode(claims, self.key, algorithm='RS256',
                                   headers={'kid': 'test-key'}),
            'access_token': 'test-only-access', 'token_type': 'Bearer',
            'scope': 'openid chatgpt.tokens.use.direct',
        }

    def test_verified_identity_allows_inference(self):
        self.assertEqual(auth_login.validate_tokens(
            self.tokens(), 'oaiapp_test', 'nonce', self.jwks,
        ), 'account-a')

    def test_unverified_identity_cannot_authorize_inference(self):
        cases = [
            {'nonce': 'other'}, {'iss': 'https://attacker.example'},
            {'aud': 'oaiapp_other'}, {'exp': 1}, {'sub': ''},
        ]
        for claims in cases:
            with self.subTest(claims=claims), self.assertRaises(ValueError):
                auth_login.validate_tokens(self.tokens(**claims), 'oaiapp_test',
                                        'nonce', self.jwks)

    def test_forged_signature_is_rejected(self):
        tokens = self.tokens()
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(tokens['id_token'], options={'verify_signature': False})
        tokens['id_token'] = jwt.encode(claims, other_key, algorithm='RS256',
                                        headers={'kid': 'test-key'})
        with self.assertRaises(ValueError):
            auth_login.validate_tokens(tokens, 'oaiapp_test', 'nonce', self.jwks)

    def test_identity_without_plan_permission_cannot_authorize_inference(self):
        tokens = self.tokens()
        tokens['scope'] = 'openid'
        with self.assertRaises(ValueError):
            auth_login.validate_tokens(tokens, 'oaiapp_test', 'nonce', self.jwks)

    def test_returning_registration_cannot_change_accounts(self):
        with self.assertRaises(ValueError):
            auth_login.validate_tokens(self.tokens(), 'oaiapp_test', 'nonce',
                                    self.jwks, subject='account-b')


class RegistrationTests(unittest.TestCase):
    def test_registration_persists_only_nonsecret_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registration.json'
            auth_login.save_registration(path, 'oaiapp_test', 'account-a')
            self.assertEqual(json.loads(path.read_text()),
                             {'client_id': 'oaiapp_test', 'subject': 'account-a'})


class LoginTests(unittest.TestCase):
    def run_login(self, directory, fail_exchange=False, invalid_nonce=False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        public['kid'] = 'login-key'
        authorization, callback_threads, callback_errors = {}, [], []

        def browser(url):
            authorization.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})

            def callback():
                try:
                    params = urlencode({'state': authorization['state'], 'code': 'test-code',
                                        'client_id': 'oaiapp_registered'})
                    with httpx.Client(trust_env=False, timeout=5) as local:
                        result = local.get(authorization['redirect_uri'] + '?' + params)
                        result.raise_for_status()
                except Exception as error:
                    callback_errors.append(error)
            thread = threading.Thread(target=callback, daemon=True)
            callback_threads.append(thread)
            thread.start()
            return True

        def provider(request):
            if request.url.path == '/api/accounts/oauth/token':
                form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
                self.assertEqual(form['client_id'], 'oaiapp_registered')
                self.assertEqual(form['redirect_uri'], authorization['redirect_uri'])
                self.assertEqual(form['code'], 'test-code')
                import base64
                import hashlib
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(form['code_verifier'].encode()).digest()
                ).rstrip(b'=').decode()
                self.assertEqual(challenge, authorization['code_challenge'])
                self.assertEqual(json.loads((directory / 'registration.json').read_text()),
                                 {'client_id': 'oaiapp_registered'})
                if fail_exchange:
                    return httpx.Response(400, json={'error': 'invalid_grant'})
                return httpx.Response(200, json={
                    'id_token': jwt.encode({
                        'iss': 'https://auth.openai.com', 'aud': 'oaiapp_registered',
                        'sub': 'account-a', 'exp': int(time.time()) + 300,
                        'nonce': 'wrong' if invalid_nonce else authorization['nonce'],
                    }, key, algorithm='RS256', headers={'kid': 'login-key'}),
                    'access_token': 'ephemeral-access-secret',
                    'refresh_token': 'ephemeral-refresh-secret',
                    'expires_in': 3600,
                    'token_type': 'Bearer', 'scope': 'openid chatgpt.tokens.use.direct',
                })
            if request.url.path == '/.well-known/jwks.json':
                return httpx.Response(200, json={'keys': [public]})
            self.fail('Unexpected provider request')

        with patch('harness_app.auth.login.webbrowser.open', side_effect=browser), \
                patch('sys.stdout', new_callable=io.StringIO), \
                httpx.Client(transport=httpx.MockTransport(provider)) as client:
            try:
                return auth_login.login(client, directory)
            finally:
                for thread in callback_threads:
                    thread.join(timeout=6)
                self.assertEqual(callback_errors, [])

    def test_browser_callback_and_pkce_exchange_return_verified_access(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_login(Path(directory)), 'ephemeral-access-secret')

    def test_verified_login_saves_renewable_credentials_separately_from_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.run_login(path)
            self.assertEqual(json.loads((path / 'registration.json').read_text()),
                             {'client_id': 'oaiapp_registered', 'subject': 'account-a'})
            saved = json.loads((path / 'credentials.json').read_text())
            self.assertEqual(saved['access_token'], 'ephemeral-access-secret')
            self.assertEqual(saved['refresh_token'], 'ephemeral-refresh-secret')

    def test_unverified_browser_identity_does_not_save_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(auth_login.HarnessError):
                self.run_login(path, invalid_nonce=True)
            self.assertFalse((path / 'credentials.json').exists())

    def test_failed_exchange_preserves_issued_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(auth_login.HarnessError):
                self.run_login(path, fail_exchange=True)
            self.assertEqual(json.loads((path / 'registration.json').read_text()),
                             {'client_id': 'oaiapp_registered'})
