"""Synthetic model responses shared by provider, agent and CLI tests."""

import json

import httpx


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
