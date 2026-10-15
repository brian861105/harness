"""Sanitized errors shared across application boundaries."""

import re


class HarnessError(ValueError):
    """A sanitized, actionable error that is safe to show in the terminal."""


def check_http(response):
    if response.is_success:
        return
    code = ''
    try:
        error = response.json().get('error', {})
        value = error.get('code', '') if isinstance(error, dict) else error
        if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_]{1,100}', value):
            code = ' (' + value + ')'
    except (ValueError, AttributeError):
        pass
    raise HarnessError('OpenAI 請求失敗：HTTP ' + str(response.status_code) + code)
