import io
import json
import unittest

from harness_app.agent import ask
from harness_app.provider import choose_model
from tests.fakes import stream_client


class ModelTests(unittest.TestCase):
    def test_default_model_uses_first_visible_account_model(self):
        catalog = {'models': [
            {'slug': 'hidden', 'visibility': 'hide'},
            {'slug': 'available-a', 'visibility': 'list'},
            {'slug': 'available-b', 'visibility': 'list'},
        ]}
        self.assertEqual(choose_model(catalog), 'available-a')

    def test_explicit_unavailable_model_is_rejected(self):
        with self.assertRaises(ValueError):
            choose_model({'models': []}, 'not-available')


class StreamTests(unittest.TestCase):
    def test_completed_stream_prints_text_from_stateless_oauth_request(self):
        requests = []
        events = [
            {'type': 'response.output_text.delta', 'delta': 'Hello'},
            {'type': 'response.completed', 'response': {'id': 'resp_test'}},
        ]
        output = io.StringIO()
        with stream_client(events, requests) as client:
            ask(client, 'test-only-access', 'Hi', 'available-a', output)
        self.assertEqual(output.getvalue(), 'Hello\n')
        self.assertEqual(json.loads(requests[0].content), {
            'model': 'available-a', 'input': [{'role': 'user', 'content': 'Hi'}],
            'store': False, 'stream': True,
        })
        self.assertEqual(str(requests[0].url), 'https://api.openai.com/v1/responses')
        self.assertEqual(requests[0].headers['authorization'], 'Bearer test-only-access')

    def test_partial_text_without_completion_is_not_success(self):
        output = io.StringIO()
        with stream_client([{'type': 'response.output_text.delta', 'delta': 'Partial'}], []) as client:
            with self.assertRaises(ValueError):
                ask(client, 'test-only-access', 'Hi', 'available-a', output)

    def test_failure_terminal_events_are_not_success(self):
        for kind in ['response.failed', 'response.incomplete', 'error']:
            with self.subTest(kind=kind), stream_client([{'type': kind}], []) as client:
                with self.assertRaises(ValueError):
                    ask(client, 'test-only-access', 'Hi', 'available-a', io.StringIO())

    def test_followup_replays_prior_output_and_returns_completed_response(self):
        requests = []
        previous = [
            {'role': 'user', 'content': 'My name is Ada'},
            {'type': 'reasoning', 'id': 'r1', 'summary': [], 'encrypted_content': 'opaque'},
            {'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': 'Hello Ada', 'annotations': []}]},
        ]
        completed = {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': 'Ada', 'annotations': []}]}],
            'usage': {'input_tokens': 20, 'output_tokens': 2, 'total_tokens': 22}}
        events = [{'type': 'response.completed', 'response': completed}]
        with stream_client(events, requests) as client:
            result = ask(client, 'test-only-access', 'What is my name?',
                                 'available-a', io.StringIO(), history=previous)
        self.assertEqual(json.loads(requests[0].content)['input'], previous + [
            {'role': 'user', 'content': 'What is my name?'}])
        self.assertEqual(result, completed)

    def test_completed_output_item_keeps_final_encrypted_reasoning(self):
        final_item = {'type': 'reasoning', 'id': 'r1', 'summary': [],
                      'encrypted_content': 'final-encrypted-value'}
        events = [
            {'type': 'response.output_item.done', 'output_index': 0, 'item': final_item},
            {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
                {'type': 'reasoning', 'id': 'r1', 'summary': []}]}},
        ]
        with stream_client(events, []) as client:
            result = ask(client, 'test-only-access', 'Hi', 'model', io.StringIO())
        self.assertEqual(result['output'][0], final_item)

    def test_refusal_delta_is_visible_in_the_stream(self):
        events = [
            {'type': 'response.refusal.delta', 'delta': 'I cannot help with that.'},
            {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
                {'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'refusal', 'refusal': 'I cannot help with that.'}]}]}},
        ]
        visible = io.StringIO()
        with stream_client(events, []) as client:
            ask(client, 'test-only-access', 'Hi', 'model', visible)
        self.assertEqual(visible.getvalue(), 'I cannot help with that.\n')

    def test_token_provider_is_resolved_before_each_request(self):
        requests = []
        tokens = iter(['first-access', 'renewed-access'])
        events = [{'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}]
        with stream_client(events, requests) as client:
            for prompt in ['First', 'Later']:
                ask(client, lambda: next(tokens), prompt, 'model', io.StringIO())
        self.assertEqual([request.headers['authorization'] for request in requests],
                         ['Bearer first-access', 'Bearer renewed-access'])
