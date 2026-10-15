import io
import json
import unittest
from unittest.mock import Mock

import httpx

from harness_app.agent import ask
from harness_app.errors import HarnessError
from tests.fakes import stream_client, tool_call


class ToolLoopTests(unittest.TestCase):
    def response(self, *items, usage=True):
        response = {'status': 'completed', 'output': list(items)}
        if usage:
            response['usage'] = {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}
        return response

    def client(self, responses, requests):
        responses = iter(responses)
        def provider(request):
            requests.append(request)
            response = next(responses)
            events = [{'type': 'response.completed', 'response': response}]
            return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))
        client = httpx.Client(transport=httpx.MockTransport(provider))
        self.addCleanup(client.close)
        return client

    def tools(self):
        from harness_app.tools.registry import TOOLS
        return Mock(definitions=TOOLS, execute=Mock(return_value={'ok': True, 'content': '1: print(1)'}))

    def test_tool_result_and_all_model_items_are_replayed_before_final_answer(self):
        call = tool_call()
        reasoning = {'type': 'reasoning', 'summary': [], 'encrypted_content': 'opaque'}
        final = {'type': 'message', 'role': 'assistant', 'content': []}
        tools, requests = self.tools(), []
        client = self.client([self.response(reasoning, call), self.response(final)], requests)
        result = ask(client, 'token', 'Read code', 'model', io.StringIO(), tools=tools)
        second = json.loads(requests[1].content)
        self.assertEqual(second['input'][:3], [
            {'role': 'user', 'content': 'Read code'}, reasoning, call])
        returned = second['input'][3]
        self.assertEqual(returned['type'], 'function_call_output')
        self.assertEqual(returned['call_id'], 'call_1')
        self.assertEqual(json.loads(returned['output']), tools.execute.return_value)
        self.assertEqual(result['output'], [reasoning, call, returned, final])
        self.assertEqual(second['tools'], tools.definitions)

    def test_tool_calls_execute_sequentially_with_their_own_arguments(self):
        calls = [tool_call('grep', {'pattern': 'foo', 'path': '.'}, 'search'),
                 tool_call('read_file', {'path': 'app.py'}, 'read')]
        tools = self.tools()
        client = self.client([self.response(*calls), self.response()], [])
        ask(client, 'token', 'Inspect', 'model', io.StringIO(), tools=tools)
        self.assertEqual(tools.execute.call_args_list, [
            unittest.mock.call('grep', {'pattern': 'foo', 'path': '.'}),
            unittest.mock.call('read_file', {'path': 'app.py'})])

    def test_each_continuation_obtains_a_current_access_token(self):
        requests, tokens = [], iter(['old', 'renewed'])
        client = self.client([self.response(tool_call()), self.response()], requests)
        ask(client, lambda: next(tokens), 'Read', 'model', io.StringIO(), tools=self.tools())
        self.assertEqual([request.headers['authorization'] for request in requests], ['Bearer old', 'Bearer renewed'])

    def test_usage_counts_all_requests_in_a_tool_turn(self):
        client = self.client([self.response(tool_call()), self.response()], [])
        result = ask(client, 'token', 'Read', 'model', io.StringIO(), tools=self.tools())
        self.assertEqual(result['usage'], {'input_tokens': 20, 'output_tokens': 4, 'total_tokens': 24})

    def test_missing_intermediate_usage_stays_unknown(self):
        client = self.client([self.response(tool_call(), usage=False), self.response()], [])
        result = ask(client, 'token', 'Read', 'model', io.StringIO(), tools=self.tools())
        self.assertIsNone(result['usage'])

    def test_invalid_json_arguments_return_a_tool_error_without_execution(self):
        call = dict(tool_call(), arguments='{invalid')
        tools, requests = self.tools(), []
        client = self.client([self.response(call), self.response()], requests)
        ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        tools.execute.assert_not_called()
        self.assertFalse(json.loads(json.loads(requests[1].content)['input'][-1]['output'])['ok'])

    def test_incomplete_stream_never_executes_a_tool(self):
        tools = self.tools()
        events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': tool_call()},
                  {'type': 'response.incomplete'}]
        with stream_client(events, []) as client, self.assertRaises(HarnessError):
            ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        tools.execute.assert_not_called()

    def test_model_request_limit_stops_before_an_unusable_final_tool_call(self):
        tools, requests = self.tools(), []
        client = self.client([self.response(tool_call(call_id='c' + str(i))) for i in range(10)], requests)
        with self.assertRaises(HarnessError):
            ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        self.assertEqual(len(requests), 10)
        self.assertEqual(tools.execute.call_count, 9)

    def test_duplicate_call_id_cannot_execute_twice(self):
        tools = self.tools()
        client = self.client([self.response(tool_call()), self.response(tool_call())], [])
        with self.assertRaises(HarnessError):
            ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        self.assertEqual(tools.execute.call_count, 1)

    def test_call_without_correlatable_id_is_rejected_before_execution(self):
        tools = self.tools()
        call = dict(tool_call(), call_id=None)
        client = self.client([self.response(call)], [])
        with self.assertRaises(HarnessError):
            ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        tools.execute.assert_not_called()

    def test_tool_call_limit_rejects_an_entire_oversized_batch(self):
        tools = self.tools()
        calls = [tool_call(call_id='c' + str(i)) for i in range(41)]
        client = self.client([self.response(*calls)], [])
        with self.assertRaises(HarnessError):
            ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
        tools.execute.assert_not_called()

    def test_malformed_completed_output_is_a_controlled_error_without_tool_execution(self):
        for output in [None, {}, [None], [42]]:
            with self.subTest(output=output):
                tools = self.tools()
                client = self.client([{'status': 'completed', 'output': output}], [])
                with self.assertRaises(HarnessError):
                    ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
                tools.execute.assert_not_called()

    def test_malformed_done_item_cannot_bypass_completed_output_validation(self):
        for index, item in [(0, None), ('bad-index', tool_call()), (-1, tool_call())]:
            with self.subTest(index=index, item=item):
                tools = self.tools()
                events = [{'type': 'response.output_item.done', 'output_index': index, 'item': item},
                          {'type': 'response.completed', 'response': self.response()}]
                with stream_client(events, []) as client, self.assertRaises(HarnessError):
                    ask(client, 'token', 'Read', 'model', io.StringIO(), tools=tools)
                tools.execute.assert_not_called()
