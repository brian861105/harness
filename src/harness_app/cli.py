"""Command-line parsing, application wiring and safe exit diagnostics."""

import argparse
import fcntl
import json
import sys
from pathlib import Path

import httpx

from .auth.login import login, read_json
from .auth.session import AuthSession
from .config import RESOURCE, STATE_DIRECTORY
from .errors import HarnessError, check_http
from .runtime import ChatRuntime
from .store import ChatStore
from .ui import run_chat


def main(argv=None, directory=None):
    parser = argparse.ArgumentParser(description='用 ChatGPT OAuth 登入，啟動 SQLite 互動對話。')
    parser.add_argument('--model', help='指定模型 slug；互動模式預設沿用工作階段的選擇。')
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error('互動模式需要終端；請在終端啟動後輸入問題。')
    directory = Path(directory) if directory is not None else STATE_DIRECTORY
    if directory.is_symlink():
        raise HarnessError('.harness 不可使用符號連結。')
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HarnessError('已有另一個練習程式正在執行，請先結束它。') from None
        with httpx.Client(timeout=60, trust_env=False) as client:
            auth = AuthSession(client, directory, browser_login=login)
            response = client.get(RESOURCE + '/models', headers={
                'Authorization': 'Bearer ' + auth.access_token(),
            })
            check_http(response)
            catalog = response.json()
            profile = read_json(directory / 'registration.json')
            owner = json.dumps([profile['client_id'], profile['subject']])
            with ChatStore(directory / 'chat.sqlite3') as store:
                runtime = ChatRuntime(client, auth.access_token, catalog, store, owner,
                                      str(Path.cwd().resolve()), requested_model=args.model,
                                      user_skills=Path.home() / '.agents' / 'skills')
                run_chat(runtime)


def run(argv=None, directory=None):
    try:
        main(argv, directory)
        return 0
    except KeyboardInterrupt:
        print('\n已取消。', file=sys.stderr)
        return 130
    except httpx.HTTPError:
        print('連線失敗或逾時，請檢查網路後重新執行。', file=sys.stderr)
        return 1
    except HarnessError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError):
        # A traceback could include callback credentials or remote response data.
        print('執行失敗；請確認瀏覽器授權、帳號方案與本機註冊資料。', file=sys.stderr)
        return 1
