import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from harness_app.runtime import ChatRuntime
from harness_app.store import ChatStore
from tests.fakes import CATALOG, USAGE, chat_client, tool_call


class RuntimeTests(unittest.TestCase):
    def runtime(self, store, results, workspace='/project'):
        requests = []
        client = chat_client(results, requests)
        self.addCleanup(client.close)
        return ChatRuntime(client, 'fake-token', CATALOG, store, 'account-a',
                           workspace), requests

    def test_resumed_session_restores_last_usage(self):
        with ChatStore(':memory:') as store:
            runtime, _ = self.runtime(store, ['Answer'])
            runtime.run_turn('Question', io.StringIO())
            resumed, _ = self.runtime(store, [])
            self.assertEqual(resumed.last_usage, USAGE)

    def test_model_change_persists_for_the_resumed_session(self):
        with ChatStore(':memory:') as store:
            runtime, _ = self.runtime(store, [])
            runtime.set_model('model-b')
            resumed, requests = self.runtime(store, ['Answer'])
            resumed.run_turn('Question', io.StringIO())
            self.assertEqual(requests[0]['model'], 'model-b')

    def test_failed_attempt_preserves_last_reported_usage(self):
        for failure in [httpx.ConnectError('offline'), KeyboardInterrupt()]:
            with self.subTest(failure=type(failure).__name__), ChatStore(':memory:') as store:
                runtime, _ = self.runtime(store, ['Answer', failure])
                runtime.run_turn('Saved', io.StringIO())
                with self.assertRaises(type(failure)):
                    runtime.run_turn('Failed', io.StringIO())
                self.assertEqual(runtime.last_usage, USAGE)

    def test_unavailable_saved_model_falls_back_to_catalog_default(self):
        with ChatStore(':memory:') as store:
            session = store.session('account-a', '/project', 'removed-model')
            runtime, _ = self.runtime(store, [])
            self.assertTrue(runtime.model_fallback)
            self.assertEqual(runtime.model, 'model-a')
            self.assertEqual(runtime.session_id, session['id'])

    def test_new_session_clears_reported_usage(self):
        with ChatStore(':memory:') as store:
            runtime, _ = self.runtime(store, ['Answer'])
            runtime.run_turn('Question', io.StringIO())
            self.assertEqual(runtime.last_usage, USAGE)
            runtime.new_session()
            self.assertIsNone(runtime.last_usage)

    def test_mutating_tools_are_declined_without_an_approval_callback(self):
        calls = [tool_call('apply_patch', {'path': 'marker', 'old_text': None,
                                         'new_text': 'created'}),
                 tool_call('run_shell', {'command': 'touch marker'})]
        for call in calls:
            with self.subTest(tool=call['name']), tempfile.TemporaryDirectory() as directory, \
                    ChatStore(':memory:') as store:
                runtime, requests = self.runtime(store, [
                    {'status': 'completed', 'output': [call]}, 'Declined'], directory)
                runtime.run_turn('Create marker', io.StringIO())
                result = json.loads(requests[1]['input'][-1]['output'])
                self.assertFalse(result['ok'])
                self.assertIn('拒絕', result['error'])
                self.assertFalse((Path(directory) / 'marker').exists())

    def test_side_effect_report_is_reset_for_the_next_attempt(self):
        with tempfile.TemporaryDirectory() as directory, ChatStore(':memory:') as store:
            call = tool_call('apply_patch', {'path': 'new.py', 'old_text': None,
                                            'new_text': 'print(1)\n'})
            runtime, _ = self.runtime(store, [
                {'status': 'completed', 'output': [call]}, httpx.ConnectError('offline'),
                httpx.ConnectError('still offline')], directory)
            with self.assertRaises(httpx.ConnectError):
                runtime.run_turn('Create file', io.StringIO(), approve=lambda name, preview: True)
            self.assertTrue(runtime.side_effects)
            with self.assertRaises(httpx.ConnectError):
                runtime.run_turn('Try again', io.StringIO())
            self.assertFalse(runtime.side_effects)
