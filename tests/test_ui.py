import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from rich.console import Console
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from harness_app.store import ChatStore
from harness_app.runtime import ChatRuntime
from harness_app.ui import StreamingMarkdown, display_text, run_chat
from tests.fakes import CATALOG, chat_client, output, tool_call


class ScriptedPrompt:
    def __init__(self, *actions):
        self.actions = iter(actions)

    def prompt(self, *args, **kwargs):
        action = next(self.actions, EOFError())
        if isinstance(action, BaseException):
            raise action
        return action


class UITests(unittest.TestCase):
    def run_chat(self, store, actions, results, workspace='/project', prompt_session=None):
        requests = []
        rendered = io.StringIO()
        with chat_client(results, requests) as client:
            runtime = ChatRuntime(client, 'test-token', CATALOG, store, 'account-a', workspace)
            run_chat(runtime,
                     prompt_session=prompt_session or ScriptedPrompt(*actions),
                     console=Console(file=rendered, width=80, color_system=None))
        return requests, rendered.getvalue()

    def tool_response(self, name, arguments):
        return {'status': 'completed', 'output': [tool_call(name, arguments)]}

    def test_model_can_edit_code_after_terminal_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, ChatStore(':memory:') as store:
            path = Path(directory) / 'app.py'
            path.write_text('before\n')
            requests, text = self.run_chat(store, ['Edit', 'y', '/exit'], [
                self.tool_response('apply_patch', {'path': 'app.py', 'old_text': 'before', 'new_text': 'after'}),
                'Changed'], workspace=directory)
            self.assertEqual(path.read_text(), 'after\n')
            self.assertIn('-before', text)
            self.assertTrue(json.loads(requests[1]['input'][-1]['output'])['ok'])

    def test_terminal_confirmation_defaults_to_declining_a_command(self):
        with tempfile.TemporaryDirectory() as directory, ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['Run', '', '/exit'], [
                self.tool_response('run_shell', {'command': 'touch marker'}), 'Declined'], workspace=directory)
            self.assertFalse((Path(directory) / 'marker').exists())
            self.assertFalse(json.loads(requests[1]['input'][-1]['output'])['ok'])

    def test_real_terminal_prompt_can_confirm_a_file_creation(self):
        with tempfile.TemporaryDirectory() as directory, ChatStore(':memory:') as store, create_pipe_input() as pipe:
            pipe.send_text('Create file\ry\r/exit\r')
            self.run_chat(store, [], [self.tool_response('apply_patch', {
                'path': 'new.py', 'old_text': None, 'new_text': 'print(1)\n'}), 'Created'],
                workspace=directory, prompt_session=PromptSession(input=pipe, output=DummyOutput()))
            self.assertEqual((Path(directory) / 'new.py').read_text(), 'print(1)\n')

    def test_full_tool_transcript_survives_sqlite_reopen_without_reexecution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'test.sh'
            script.write_text('echo once >> count\n')
            database = root / '.harness' / 'chat.sqlite3'
            database.parent.mkdir()
            with ChatStore(database) as store:
                turn_requests, _ = self.run_chat(store, ['Run script', 'y', '/exit'], [
                    self.tool_response('run_shell', {'command': 'bash ./test.sh'}), 'Done'], workspace=directory)
            with ChatStore(database) as store:
                requests, _ = self.run_chat(store, ['What happened?', '/exit'], ['Tests ran'], workspace=directory)
            self.assertEqual(script.with_name('count').read_text(), 'once\n')
            replay = requests[0]['input']
            self.assertEqual(replay, turn_requests[1]['input'] + output('Done') + [
                {'role': 'user', 'content': 'What happened?'}])

    def test_failed_turn_warns_that_approved_edits_remain(self):
        with tempfile.TemporaryDirectory() as directory, ChatStore(':memory:') as store:
            path = Path(directory) / 'app.py'
            path.write_text('before')
            _, text = self.run_chat(store, ['Edit', 'y', '/exit'], [
                self.tool_response('apply_patch', {'path': 'app.py', 'old_text': 'before', 'new_text': 'after'}),
                httpx.ConnectError('offline')], workspace=directory)
            self.assertEqual(path.read_text(), 'after')
            self.assertIn('不會自動復原', text)
            self.assertEqual(store.prompts('account-a', directory), [])

    def test_malformed_model_output_returns_to_input_without_committing_it(self):
        with ChatStore(':memory:') as store:
            requests, text = self.run_chat(store, ['Read', 'Retry', '/exit'], [
                {'status': 'completed', 'output': [None]}, 'Done'])
            self.assertEqual(requests[1]['input'], [{'role': 'user', 'content': 'Retry'}])
            self.assertIn('輸出格式不正確', text)

    def test_completed_turn_displays_reported_usage(self):
        with ChatStore(':memory:') as store:
            _, text = self.run_chat(store, ['Question', '/exit'], ['Answer'])
            self.assertIn('本次 token：輸入 10 / 輸出 2 / 合計 12', text)

    def test_new_command_excludes_previous_context(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['First', '/new', 'Fresh', '/exit'], ['One', 'Two'])
            self.assertEqual(requests[1]['input'], [{'role': 'user', 'content': 'Fresh'}])

    def test_invalid_model_does_not_replace_the_active_model(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['/model missing', 'Hi', '/exit'], ['Hello'])
            self.assertEqual(requests[0]['model'], 'model-a')
            self.assertEqual(store.session('account-a', '/project', 'fallback')['model'], 'model-a')

    def test_valid_model_command_applies_to_the_next_turn(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['/model model-b', 'Hi', '/exit'], ['Hello'])
            self.assertEqual(requests[0]['model'], 'model-b')

    def test_interrupt_after_partial_output_closes_stream_without_saving_it(self):
        class InterruptedStream(httpx.SyncByteStream):
            closed = False

            def __iter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"Partial"}\n\n'
                raise KeyboardInterrupt()

            def close(self):
                self.closed = True

        stream = InterruptedStream()
        response = httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['Saved', 'Cancelled', 'Retry', '/exit'], [
                'Previous answer', response, 'Done'])
            self.assertEqual(requests[2]['input'], [{'role': 'user', 'content': 'Saved'}]
                             + output('Previous answer') + [{'role': 'user', 'content': 'Retry'}])
            self.assertTrue(stream.closed)
            self.assertEqual(store.prompts('account-a', '/project'), ['Saved', 'Retry'])

    def test_network_failure_returns_to_input_without_committing_a_turn(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['Saved', 'Failed', 'Retry', '/exit'], [
                'Previous answer', httpx.ConnectError('offline'), 'Done'])
            self.assertEqual(requests[2]['input'], [{'role': 'user', 'content': 'Saved'}]
                             + output('Previous answer') + [{'role': 'user', 'content': 'Retry'}])

    def test_help_and_model_list_do_not_call_the_provider(self):
        with ChatStore(':memory:') as store:
            requests, text = self.run_chat(store, ['/help', '/model', '/exit'], [])
            self.assertEqual(requests, [])
            self.assertIn('/new', text)
            self.assertIn('model-b', text)

    def test_input_interrupt_clears_input_and_eof_exits(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, [KeyboardInterrupt(), EOFError()], [])
            self.assertEqual(requests, [])

    def test_interrupt_after_commit_reports_the_turn_as_saved(self):
        stop = StreamingMarkdown.__exit__

        def interrupt_after_stop(writer, *args):
            stop(writer, *args)
            raise KeyboardInterrupt()

        with ChatStore(':memory:') as store, \
                patch.object(StreamingMarkdown, '__exit__', interrupt_after_stop):
            _, text = self.run_chat(store, ['Question', '/exit'], ['Answer'])
            self.assertEqual(store.prompts('account-a', '/project'), ['Question'])
            self.assertIn('本輪已完成並儲存', text)
            self.assertNotIn('未儲存', text)

    def test_terminal_controls_are_removed_from_display(self):
        self.assertEqual(display_text('\x1b[31mHello\x1b[0m\r\x07'), 'Hello')

    def test_real_prompt_accepts_help_then_exit(self):
        rendered = io.StringIO()
        with ChatStore(':memory:') as store, create_pipe_input() as pipe:
            pipe.send_text('/help\r/exit\r')
            with httpx.Client(transport=httpx.MockTransport(
                    lambda request: self.fail('Commands must not call the model'))) as client:
                runtime = ChatRuntime(client, 'test-token', CATALOG, store, 'account', '/project')
                run_chat(runtime,
                         prompt_session=PromptSession(input=pipe, output=DummyOutput()),
                         console=Console(file=rendered, width=80, color_system=None))
        self.assertIn('/new', rendered.getvalue())

    def test_default_prompt_supports_alt_enter_multiline(self):
        requests = []
        rendered = io.StringIO()
        with ChatStore(':memory:') as store, create_pipe_input() as pipe:
            pipe.send_text('first\x1b\rsecond\r/exit\r')
            def session_factory(**kwargs):
                return PromptSession(input=pipe, output=DummyOutput(), **kwargs)
            with patch('harness_app.ui.PromptSession', side_effect=session_factory), \
                    chat_client(['Done'], requests) as client:
                runtime = ChatRuntime(client, 'test-token', CATALOG, store, 'account', '/project')
                run_chat(runtime,
                         console=Console(file=rendered, width=80, color_system=None))
        self.assertEqual(requests[0]['input'], [{'role': 'user', 'content': 'first\nsecond'}])


if __name__ == '__main__':
    unittest.main()
