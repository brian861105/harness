import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import httpx

from harness_app.auth.session import AuthSession, save_credentials
from harness_app.errors import HarnessError


TOKENS = {'access_token': 'test-access', 'refresh_token': 'test-refresh',
          'token_type': 'Bearer', 'expires_in': 3600,
          'scope': 'openid offline_access resource.invoke chatgpt.tokens.use.direct'}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / 'registration.json').write_text(json.dumps({
            'client_id': 'oaiapp_test', 'subject': 'account-a'}))
        (self.directory / 'host.json').write_text(json.dumps({'ext_agent_host_id': 'urn:uuid:host'}))
        self.clock = patch('harness_app.auth.session.time.time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def save(self, **overrides):
        tokens = dict(TOKENS, **overrides)
        save_credentials(self.directory, 'oaiapp_test', 'account-a', 'urn:uuid:host', tokens)

    def client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_valid_saved_login_is_reused_after_restart_without_network_or_browser(self):
        self.save()
        browser = Mock(side_effect=AssertionError('unexpected browser login'))
        client = self.client(lambda request: self.fail('unexpected token exchange'))
        self.assertEqual(AuthSession(client, self.directory, browser).access_token(), 'test-access')

    def test_refresh_uses_issued_id_and_persists_rotated_tokens_for_restart(self):
        self.save(expires_in=10)
        def provider(request):
            self.assertEqual(str(request.url), 'https://auth.openai.com/api/accounts/oauth/token')
            self.assertEqual(parse_qs(request.content.decode()), {
                'grant_type': ['refresh_token'], 'client_id': ['oaiapp_test'],
                'refresh_token': ['test-refresh'], 'resource': ['https://api.openai.com/v1']})
            return httpx.Response(200, json=dict(TOKENS, access_token='new-access', refresh_token='new-refresh'))
        client = self.client(provider)
        browser = Mock(side_effect=AssertionError('unexpected browser login'))
        self.assertEqual(AuthSession(client, self.directory, browser).access_token(), 'new-access')
        client = self.client(lambda request: self.fail('new credentials must survive restart'))
        self.assertEqual(AuthSession(client, self.directory, browser).access_token(), 'new-access')
        saved = json.loads((self.directory / 'credentials.json').read_text())
        self.assertEqual(saved['refresh_token'], 'new-refresh')

    def test_terminal_refresh_failure_performs_one_new_browser_login(self):
        self.save(expires_in=10)
        client = self.client(lambda request: httpx.Response(400, json={'error': 'invalid_grant'}))
        visits = []
        def browser(client, directory):
            visits.append(directory)
            self.save(access_token='reauthorized', refresh_token='replacement')
            return 'reauthorized'
        self.assertEqual(AuthSession(client, self.directory, browser).access_token(), 'reauthorized')
        self.assertEqual(visits, [self.directory])

    def test_temporary_refresh_failure_preserves_credentials_without_browser(self):
        self.save(expires_in=10)
        path = self.directory / 'credentials.json'
        before = path.read_bytes()
        browser = Mock(side_effect=AssertionError('unexpected browser login'))
        client = self.client(lambda request: httpx.Response(503, json={'error': 'temporarily_unavailable'}))
        with self.assertRaises(HarnessError):
            AuthSession(client, self.directory, browser).access_token()
        self.assertEqual(path.read_bytes(), before)

    def test_network_failure_does_not_erase_saved_login(self):
        self.save(expires_in=10)
        path = self.directory / 'credentials.json'
        before = path.read_bytes()
        def provider(request):
            raise httpx.ConnectError('offline')
        client = self.client(provider)
        with self.assertRaises(httpx.HTTPError):
            AuthSession(client, self.directory, Mock()).access_token()
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_client_reports_configuration_code_and_preserves_login(self):
        self.save(expires_in=10)
        path = self.directory / 'credentials.json'
        before = path.read_bytes()
        client = self.client(lambda request: httpx.Response(400, json={'error': 'invalid_client'}))
        browser = Mock(side_effect=AssertionError('unexpected browser login'))
        with self.assertRaises(HarnessError) as error:
            AuthSession(client, self.directory, browser).access_token()
        self.assertIn('invalid_client', str(error.exception))
        self.assertNotIn('暫時', str(error.exception))
        self.assertEqual(path.read_bytes(), before)

    def test_unsafe_remote_error_code_is_not_displayed(self):
        self.save(expires_in=10)
        client = self.client(lambda request: httpx.Response(400, json={
            'error': {'code': 'private remote text\nwith controls'}}))
        with self.assertRaises(HarnessError) as error:
            AuthSession(client, self.directory, Mock()).access_token()
        self.assertNotIn('private remote text', str(error.exception))

    def test_malformed_refresh_response_preserves_saved_login(self):
        for response in [dict(TOKENS, token_type=None), dict(TOKENS, expires_in=False),
                         dict(TOKENS, refresh_token=None), {'access_token': 'incomplete'}]:
            with self.subTest(response_fields=list(response)):
                self.save(expires_in=10)
                path = self.directory / 'credentials.json'
                before = path.read_bytes()
                client = self.client(lambda request: httpx.Response(200, json=response))
                with self.assertRaises(HarnessError):
                    AuthSession(client, self.directory, Mock()).access_token()
                self.assertEqual(path.read_bytes(), before)

    def test_nonterminal_refresh_error_does_not_trigger_browser_login(self):
        self.save(expires_in=10)
        client = self.client(lambda request: httpx.Response(400, json={'error': {'code': []}}))
        with self.assertRaises(HarnessError):
            AuthSession(client, self.directory, Mock(side_effect=AssertionError('unexpected login'))).access_token()

    def test_long_running_session_renews_before_a_later_request(self):
        self.save()
        calls = []
        def provider(request):
            calls.append(request)
            return httpx.Response(200, json=dict(TOKENS, access_token='later-access', refresh_token='later-refresh'))
        session = AuthSession(self.client(provider), self.directory, Mock())
        self.assertEqual(session.access_token(), 'test-access')
        with patch('harness_app.auth.session.time.time', return_value=4600):
            self.assertEqual(session.access_token(), 'later-access')
        self.assertEqual(len(calls), 1)

    def test_refresh_retains_granted_scope_when_server_omits_it(self):
        self.save(expires_in=10)
        response = dict(TOKENS, access_token='new-access', refresh_token='new-refresh')
        del response['scope']
        session = AuthSession(self.client(lambda request: httpx.Response(200, json=response)),
                              self.directory, Mock())
        self.assertEqual(session.access_token(), 'new-access')

    def test_mismatched_registration_cannot_use_saved_credentials(self):
        self.save()
        (self.directory / 'registration.json').write_text(json.dumps({
            'client_id': 'oaiapp_other', 'subject': 'account-b'}))
        client = self.client(lambda request: self.fail('mismatched credentials must not reach network'))
        with self.assertRaises(HarnessError):
            AuthSession(client, self.directory, Mock()).access_token()

    def test_credentials_are_owner_only_and_exclude_unneeded_id_token(self):
        self.save(id_token='test-id-token')
        path = self.directory / 'credentials.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('id_token', json.loads(path.read_text()))

    def test_symlink_credentials_are_rejected_without_reading_target(self):
        target = self.directory / 'target.json'
        target.write_text('not-a-credential')
        (self.directory / 'credentials.json').symlink_to(target)
        client = self.client(lambda request: self.fail('unexpected network'))
        with self.assertRaises(HarnessError):
            AuthSession(client, self.directory, Mock()).access_token()
        self.assertEqual(target.read_text(), 'not-a-credential')

    def test_broadly_readable_credentials_are_rejected(self):
        self.save()
        os.chmod(self.directory / 'credentials.json', 0o644)
        client = self.client(lambda request: self.fail('unexpected network'))
        with self.assertRaises(HarnessError):
            AuthSession(client, self.directory, Mock()).access_token()


if __name__ == '__main__':
    unittest.main()
