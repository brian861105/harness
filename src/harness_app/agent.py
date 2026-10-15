"""Sequential model/tool loop and complete turn transcripts."""

import json

from .errors import HarnessError
from .provider import request_response


def ask(client, access_token, prompt, model, output, history=None, tools=None):
    items = list(history or []) + [{'role': 'user', 'content': prompt}]
    if tools is None:
        return request_response(client, access_token, items, model, output)
    transcript, usages, seen_calls = [], [], set()
    for step in range(10):
        response = request_response(client, access_token, items, model, output, tools)
        if response.get('status') != 'completed' or not isinstance(response.get('output'), list):
            raise HarnessError('模型沒有提供完整的工具回應。')
        batch = response['output']
        calls = [item for item in batch if item.get('type') == 'function_call']
        transcript.extend(batch)
        items.extend(batch)
        usages.append(response.get('usage'))
        if not calls:
            fields = ('input_tokens', 'output_tokens', 'total_tokens')
            known = all(isinstance(usage, dict) and all(type(usage.get(key)) is int
                        for key in fields) for usage in usages)
            usage = {key: sum(item[key] for item in usages) for key in fields} if known else None
            return dict(response, output=transcript, usage=usage)
        if step == 9 or len(seen_calls) + len(calls) > 40:
            raise HarnessError('已達本輪工具執行上限，請縮小任務後再試。')
        ids = [call.get('call_id') for call in calls]
        if (any(not isinstance(value, str) or not value or value in seen_calls for value in ids)
                or len(set(ids)) != len(ids)):
            raise HarnessError('工具呼叫識別碼遺失或重複，已停止執行。')
        seen_calls.update(ids)
        for call in calls:
            try:
                arguments = json.loads(call.get('arguments', ''))
            except (TypeError, ValueError):
                result = {'ok': False, 'error': '工具 arguments 必須是有效的 JSON 物件。'}
            else:
                result = tools.execute(call.get('name'), arguments)
            returned = {'type': 'function_call_output', 'call_id': call['call_id'],
                        'output': json.dumps(result, ensure_ascii=False)}
            items.append(returned)
            transcript.append(returned)
