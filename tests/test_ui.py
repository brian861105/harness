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
from harness_app.ui import display_text, run_chat

CATALOG = {'models': [{'slug': 'model-a', 'visibility': 'list'},
                       {'slug': 'model-b', 'visibility': 'list'}]}


class ScriptedPrompt:
    def __init__(self, *actions):
        self.actions = iter(actions)

    def prompt(self, *args, **kwargs):
        action = next(self.actions, EOFError())
        if isinstance(action, BaseException):
            raise action
        return action


def output(text):
    return [{'type': 'message', 'role': 'assistant', 'content': [
        {'type': 'output_text', 'text': text, 'annotations': []}]}]


class UITests(unittest.TestCase):
    def run_chat(self, store, actions, results, workspace='/project', prompt_session=None):
        requests = []
        responses = iter(results)
        rendered = io.StringIO()

        def provider(request):
            requests.append(json.loads(request.content))
            item = next(responses)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, httpx.Response):
                return item
            if isinstance(item, int):
                return httpx.Response(item, json={'error': {'code': 'temporarily_unavailable'}})
            if isinstance(item, dict):
                response, events = item, []
            else:
                response = {'status': 'completed', 'output': output(item),
                            'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}
                events = [{'type': 'response.output_text.delta', 'delta': item}]
            events.append({'type': 'response.completed', 'response': response})
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))

        with httpx.Client(transport=httpx.MockTransport(provider)) as client:
            run_chat(client, 'test-token', CATALOG, store, 'account-a', workspace,
                     prompt_session=prompt_session or ScriptedPrompt(*actions),
                     console=Console(file=rendered, width=80, color_system=None))
        return requests, rendered.getvalue()

    def tool_response(self, name, arguments):
        return {'status': 'completed', 'output': [{'type': 'function_call',
                'call_id': 'call_' + name, 'name': name, 'arguments': json.dumps(arguments)}]}

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
                self.run_chat(store, ['Run script', 'y', '/exit'], [
                    self.tool_response('run_shell', {'command': 'bash ./test.sh'}), 'Done'], workspace=directory)
            with ChatStore(database) as store:
                requests, _ = self.run_chat(store, ['What happened?', '/exit'], ['Tests ran'], workspace=directory)
            self.assertEqual(script.with_name('count').read_text(), 'once\n')
            replay = requests[0]['input']
            self.assertEqual([item.get('type') for item in replay[1:3]], ['function_call', 'function_call_output'])
            self.assertEqual(json.loads(replay[2]['output'])['exit_code'], 0)

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

    def test_followup_request_contains_committed_conversation(self):
        with ChatStore(':memory:') as store:
            requests, text = self.run_chat(store, ['My name is Ada', 'My name?', '/exit'], ['Hello Ada', 'Ada'])
            self.assertEqual(requests[1]['input'], [
                {'role': 'user', 'content': 'My name is Ada'}] + output('Hello Ada') + [
                {'role': 'user', 'content': 'My name?'}])
            self.assertIn('12', text)

    def test_restart_replays_the_previous_session(self):
        with ChatStore(':memory:') as store:
            self.run_chat(store, ['Hi', '/exit'], ['Hello'])
            requests, _ = self.run_chat(store, ['Again', '/exit'], ['Welcome back'])
            self.assertEqual(requests[0]['input'][0], {'role': 'user', 'content': 'Hi'})

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

    def test_cancelled_request_is_not_replayed_on_retry(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['Cancelled', 'Retry', '/exit'], [KeyboardInterrupt(), 'Done'])
            self.assertEqual(requests[1]['input'], [{'role': 'user', 'content': 'Retry'}])
            self.assertEqual(store.prompts('account-a', '/project'), ['Retry'])

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
            requests, _ = self.run_chat(store, ['Cancelled', 'Retry', '/exit'], [response, 'Done'])
            self.assertEqual(requests[1]['input'], [{'role': 'user', 'content': 'Retry'}])
            self.assertTrue(stream.closed)

    def test_network_failure_returns_to_input_without_committing_a_turn(self):
        with ChatStore(':memory:') as store:
            requests, _ = self.run_chat(store, ['Failed', 'Retry', '/exit'], [httpx.ConnectError('offline'), 'Done'])
            self.assertEqual(requests[1]['input'], [{'role': 'user', 'content': 'Retry'}])

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

    def test_terminal_controls_are_removed_from_display(self):
        self.assertEqual(display_text('\x1b[31mHello\x1b[0m\r\x07'), 'Hello')

    def test_real_prompt_accepts_help_then_exit(self):
        rendered = io.StringIO()
        with ChatStore(':memory:') as store, create_pipe_input() as pipe:
            pipe.send_text('/help\r/exit\r')
            with httpx.Client(transport=httpx.MockTransport(
                    lambda request: self.fail('Commands must not call the model'))) as client:
                run_chat(client, 'test-token', CATALOG, store, 'account', '/project',
                         prompt_session=PromptSession(input=pipe, output=DummyOutput()),
                         console=Console(file=rendered, width=80, color_system=None))
        self.assertIn('/new', rendered.getvalue())

    def test_default_prompt_supports_alt_enter_multiline(self):
        requests = []
        rendered = io.StringIO()

        def provider(request):
            requests.append(json.loads(request.content))
            event = {'type': 'response.completed', 'response': {
                'status': 'completed', 'output': output('Done')}}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  text='data: ' + json.dumps(event) + '\n\n')

        with ChatStore(':memory:') as store, create_pipe_input() as pipe:
            pipe.send_text('first\x1b\rsecond\r/exit\r')
            def session_factory(**kwargs):
                return PromptSession(input=pipe, output=DummyOutput(), **kwargs)
            with patch('harness_app.ui.PromptSession', side_effect=session_factory), \
                    httpx.Client(transport=httpx.MockTransport(provider)) as client:
                run_chat(client, 'test-token', CATALOG, store, 'account', '/project',
                         console=Console(file=rendered, width=80, color_system=None))
        self.assertEqual(requests[0]['input'], [{'role': 'user', 'content': 'first\nsecond'}])


if __name__ == '__main__':
    unittest.main()
