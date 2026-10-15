"""Command-line parsing, application wiring and safe exit diagnostics."""

import argparse
import fcntl
import json
import sys
from pathlib import Path

import httpx

from .agent import ask
from .auth.login import login, read_json
from .auth.session import AuthSession
from .config import RESOURCE, STATE_DIRECTORY
from .errors import HarnessError, check_http
from .provider import choose_model


def main(argv=None, directory=None):
    parser = argparse.ArgumentParser(description='用 ChatGPT OAuth 登入，啟動 SQLite 互動對話。')
    parser.add_argument('prompt', nargs='?', help='提供問題時只回答一次；省略則進入互動介面。')
    parser.add_argument('--model', help='指定模型 slug；互動模式預設沿用工作階段的選擇。')
    args = parser.parse_args(argv)
    if args.prompt is not None and not args.prompt.strip():
        parser.error('問題不可留空。')
    if args.prompt is None and not sys.stdin.isatty():
        parser.error('互動模式需要終端；單次提問請直接提供問題。')
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
            if args.prompt is not None:
                from .permissions import confirm_tool
                from .tools.registry import ProjectTools
                model = choose_model(catalog, args.model)
                print('使用模型：' + model, file=sys.stderr)
                tools = ProjectTools(Path.cwd(), approve=confirm_tool,
                                     notify=lambda name, detail: print('工具 ' + name, file=sys.stderr))
                try:
                    ask(client, auth.access_token, args.prompt, model, sys.stdout, tools=tools)
                except BaseException:
                    if tools.side_effects:
                        print('已執行的工具操作不會自動復原；請先檢查專案狀態。', file=sys.stderr)
                    raise
            else:
                from .store import ChatStore
                from .ui import run_chat
                profile = read_json(directory / 'registration.json')
                owner = json.dumps([profile['client_id'], profile['subject']])
                with ChatStore(directory / 'chat.sqlite3') as store:
                    run_chat(client, auth.access_token, catalog, store, owner,
                             str(Path.cwd().resolve()), requested_model=args.model)


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
