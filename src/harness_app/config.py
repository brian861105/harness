"""Service endpoints and the source checkout's state location."""

from pathlib import Path

AUTH = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
STATE_DIRECTORY = Path(__file__).resolve().parents[2] / '.harness'
