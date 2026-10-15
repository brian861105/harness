import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from harness_app import cli
from harness_app.auth.login import save_registration
from harness_app.auth.session import save_credentials


class CLITests(unittest.TestCase):
    def test_script_entrypoint_exposes_unavailable_model_as_its_public_error(self):
        with tempfile.TemporaryDirectory() as directory:
            def login(client, state_directory):
                save_registration(state_directory / 'registration.json', 'test-client', 'test-subject')
                (state_directory / 'host.json').write_text(json.dumps({'ext_agent_host_id': 'test-host'}))
                save_credentials(state_directory, 'test-client', 'test-subject', 'test-host', {
                    'access_token': 'fake-token', 'refresh_token': 'fake-refresh',
                    'expires_in': 3600, 'scope': 'chatgpt.tokens.use.direct', 'token_type': 'Bearer'})
                return 'fake-token'
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                'models': [{'slug': 'model-a', 'visibility': 'list'}]})))
            self.addCleanup(client.close)
            launcher = Path(__file__).resolve().parents[1] / 'harness.py'
            with patch('harness_app.cli.STATE_DIRECTORY', Path(directory) / '.harness'), \
                    patch('harness_app.cli.login', side_effect=login), \
                    patch('harness_app.cli.httpx.Client', return_value=client), \
                    patch('sys.argv', ['harness.py', '--model', 'missing']), \
                    patch('sys.stdin.isatty', return_value=True), \
                    patch('sys.stderr', new_callable=io.StringIO) as diagnostic:
                with self.assertRaises(SystemExit) as caught:
                    runpy.run_path(str(launcher), run_name='__main__')
            self.assertEqual(caught.exception.code, 1)
            self.assertIn('model-a', diagnostic.getvalue())

    def test_positional_prompt_is_rejected_before_state_creation_or_login(self):
        self.check_early_rejection(['Question'], terminal=True)

    def test_nonterminal_stdin_is_rejected_before_state_creation_or_login(self):
        self.check_early_rejection([], terminal=False)

    def check_early_rejection(self, arguments, terminal):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / '.harness'
            with patch('harness_app.cli.AuthSession', side_effect=AssertionError('must not log in')) as auth, \
                    patch('sys.stdin.isatty', return_value=terminal), \
                    patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(arguments, directory=state)
            self.assertEqual(caught.exception.code, 2)
            auth.assert_not_called()
            self.assertFalse(state.exists())
