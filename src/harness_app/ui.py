"""Interactive terminal chat backed by SQLite."""

import re

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from .agent import ask
from .errors import HarnessError
from .permissions import confirm_tool
from .provider import choose_model
from .tools.registry import ProjectTools

COMMANDS = ['/help', '/model', '/new', '/exit']


def display_text(text):
    text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', text)
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', text)


def usage_text(usage):
    if not usage:
        return '本次 token：未回報'
    return '本次 token：輸入 {} / 輸出 {} / 合計 {}'.format(
        usage.get('input_tokens', '—'), usage.get('output_tokens', '—'),
        usage.get('total_tokens', '—'))


class StreamingMarkdown:
    def __init__(self, console):
        self.text = ''
        self.live = Live(Spinner('dots', text='正在回答…'), console=console,
                         refresh_per_second=8, vertical_overflow='visible')

    def __enter__(self):
        self.live.start()
        return self

    def write(self, text):
        self.text += display_text(text)
        self.live.update(Markdown(self.text))
        return len(text)

    def flush(self):
        pass  # Live refreshes at a bounded rate instead of on every token.

    def notify(self, name, detail):
        self.live.console.print(Text('工具 ' + name + ' · ' + display_text(detail), style='dim'))

    def approve(self, name, preview, prompt_session):
        self.live.stop()
        try:
            return confirm_tool(name, preview, console=self.live.console,
                                reader=lambda: prompt_session.prompt('允許這次操作？[y/N] '))
        finally:
            self.live.start(refresh=True)

    def __exit__(self, *args):
        self.live.stop()


def show_turn(console, turn):
    console.print(Text('› ' + display_text(turn['prompt']), style='bold cyan'))
    for item in turn['output']:
        if item.get('type') == 'message':
            text = '\n'.join(part.get('text', part.get('refusal', ''))
                             for part in item.get('content', []))
            console.print(Markdown(display_text(text)))
    console.print()


def run_chat(client, access_token, catalog, store, owner, workspace,
             requested_model=None, prompt_session=None, console=None):
    console = console or Console()
    default_model = choose_model(catalog, requested_model)
    session = store.session(owner, workspace, default_model)
    try:
        model = choose_model(catalog, requested_model or session['model'])
    except HarnessError:
        model = default_model
        console.print('原本的模型目前不可用，已改用：' + display_text(model))
    store.set_model(session['id'], model)
    turns = store.turns(session['id'])
    last_usage = turns[-1]['usage'] if turns else None
    console.print(Panel(Text('model: ' + display_text(model) + '\n'
                            'directory: ' + display_text(workspace) + '\n'
                            'ChatGPT plan · session ' + session['id'][:8]),
                        title='Python Harness', border_style='cyan'))
    if turns:
        console.print('已接續 {} 輪對話，顯示最近三輪。'.format(len(turns)), style='dim')
        for turn in turns[-3:]:
            show_turn(console, turn)
    console.print('/help 查看指令 · Enter 送出 · Alt+Enter 換行 · Ctrl+D 離開', style='dim')
    console.print('已啟用讀檔、搜尋、修改及 Bash；修改與命令會逐次確認。', style='dim')

    if prompt_session is None:
        bindings = KeyBindings()

        @bindings.add('escape', 'enter')
        def newline(event):
            event.current_buffer.insert_text('\n')

        prompt_session = PromptSession(
            history=InMemoryHistory(history_strings=store.prompts(owner, workspace)),
            completer=WordCompleter(COMMANDS, sentence=True),
            complete_while_typing=False, key_bindings=bindings,
        )

    while True:
        try:
            prompt = prompt_session.prompt('› ', bottom_toolbar=lambda: [('',
                display_text(model) + ' · ' + session['id'][:8] + ' · ' + usage_text(last_usage))])
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        if not prompt.strip():
            continue
        if prompt.startswith('/'):
            command, _, argument = prompt.strip().partition(' ')
            argument = argument.strip()
            if command == '/exit':
                break
            if command == '/help':
                console.print(Text('/help  指令說明\n/model [slug]  列出或切換模型\n'
                                   '/new  開新對話，保留舊紀錄\n/exit  離開'))
            elif command == '/model':
                if not argument:
                    for item in catalog['models']:
                        if item.get('visibility') == 'list':
                            console.print(Text(('• ' if item['slug'] == model else '  ') + display_text(item['slug'])))
                else:
                    try:
                        candidate = choose_model(catalog, argument)
                    except HarnessError as error:
                        console.print(Text(display_text(str(error)), style='yellow'))
                        continue
                    store.set_model(session['id'], candidate)
                    model = candidate
                    console.print(Text('模型已切換：' + display_text(model)))
            elif command == '/new':
                session = store.session(owner, workspace, model, new=True)
                last_usage = None
                console.print(Text('已開啟新對話：' + session['id'][:8], style='cyan'))
            else:
                console.print('未知指令，請輸入 /help。', style='yellow')
            continue

        tools = None
        saved = False
        try:
            with StreamingMarkdown(console) as writer:
                tools = ProjectTools(workspace, notify=writer.notify,
                                     approve=lambda name, preview: writer.approve(name, preview, prompt_session))
                response = ask(client, access_token, prompt, model, writer,
                               history=store.inputs(session['id']), tools=tools)
            store.append_turn(session['id'], prompt, response, model)
            saved = True
        except KeyboardInterrupt:
            console.print('\n已停止本次生成，本輪未儲存。', style='yellow')
            continue
        except httpx.HTTPError:
            console.print('\n連線失敗或逾時，本輪未儲存；可重新輸入。', style='yellow')
            continue
        except HarnessError as error:
            console.print(Text('\n' + display_text(str(error)) + ' 本輪未儲存。', style='yellow'))
            continue
        finally:
            if tools is not None and tools.side_effects and not saved:
                console.print('已執行的工具操作不會自動復原；再次嘗試前請檢查檔案與命令結果。', style='yellow')
        last_usage = response.get('usage')
        console.print(Text(usage_text(last_usage), style='dim'))
        console.print()
