import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from harness_app import cli
from harness_app.auth.login import save_registration
from harness_app.auth.session import save_credentials
from tests.fakes import tool_call


class CLIToolTests(unittest.TestCase):
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

    def test_single_shot_rejects_shell_calls_when_stdin_is_not_a_terminal(self):
        requests = []
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker'
            call = tool_call('run_shell', {'command': 'touch ' + str(marker)})
            responses = iter([{'status': 'completed', 'output': [call]},
                              {'status': 'completed', 'output': []}])
            def provider(request):
                if request.method == 'GET':
                    return httpx.Response(200, json={'models': [{'slug': 'model', 'visibility': 'list'}]})
                requests.append(json.loads(request.content))
                event = {'type': 'response.completed', 'response': next(responses)}
                return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\n')
            client = httpx.Client(transport=httpx.MockTransport(provider))
            self.addCleanup(client.close)
            with patch('harness_app.cli.AuthSession', return_value=Mock(access_token=Mock(return_value='fake-token'))), \
                    patch('harness_app.cli.httpx.Client', return_value=client), \
                    patch('sys.stdin.isatty', return_value=False), \
                    patch('sys.stdout', new_callable=io.StringIO), \
                    patch('sys.stderr', new_callable=io.StringIO), \
                    patch('builtins.input', side_effect=AssertionError('must not prompt')):
                cli.main(['Run the test'], directory=Path(directory) / '.harness')
            self.assertEqual(len(requests), 2)
            self.assertFalse(json.loads(requests[1]['input'][-1]['output'])['ok'])
            self.assertFalse(marker.exists())
