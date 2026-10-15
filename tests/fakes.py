"""Synthetic providers for offline model, runtime, and terminal tests."""

import json

import httpx


CATALOG = {'models': [{'slug': 'model-a', 'visibility': 'list'},
                      {'slug': 'model-b', 'visibility': 'list'}]}
USAGE = {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}


def output(text):
    return [{'type': 'message', 'role': 'assistant', 'content': [
        {'type': 'output_text', 'text': text, 'annotations': []}]}]


def chat_client(results, requests):
    results = iter(results)

    def provider(request):
        requests.append(json.loads(request.content))
        result = next(results)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, httpx.Response):
            return result
        events = []
        if isinstance(result, str):
            events.append({'type': 'response.output_text.delta', 'delta': result})
            result = {'status': 'completed', 'output': output(result), 'usage': USAGE}
        events.append({'type': 'response.completed', 'response': result})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=''.join('data: ' + json.dumps(event) + '\n\n' for event in events))

    return httpx.Client(transport=httpx.MockTransport(provider))


def stream_client(events, requests):
    def handle(request):
        requests.append(request)
        data = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=data)
    return httpx.Client(transport=httpx.MockTransport(handle))


def tool_call( name='read_file', arguments=None, call_id='call_1'):
    return {'type': 'function_call', 'id': 'fc_' + call_id, 'call_id': call_id,
            'name': name, 'arguments': json.dumps(arguments or {'path': 'app.py'})}
