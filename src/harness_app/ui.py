"""Terminal input, streaming display, and approval presentation."""

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

from .errors import HarnessError
from .permissions import confirm_tool

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
    console.print(Text('› ' + display_text(turn['display']), style='bold cyan'))
    for item in turn['output']:
        if item.get('type') == 'message':
            text = '\n'.join(part.get('text', part.get('refusal', ''))
                             for part in item.get('content', []))
            console.print(Markdown(display_text(text)))
    console.print()


def context_text(context):
    skills = len(context['skills'])
    return ('AGENTS.md' if context['agents'] else 'no AGENTS.md') + ' · ' + str(skills) + ' skill' + ('' if skills == 1 else 's')


def show_context_warnings(console, context):
    for warning in context['warnings']:
        console.print(Text(display_text(warning), style='yellow'))


def run_chat(runtime, prompt_session=None, console=None):
    console = console or Console()
    if runtime.model_fallback:
        console.print('原本的模型目前不可用，已改用：' + display_text(runtime.model))
    turns = runtime.turns()
    console.print(Panel(Text('model: ' + display_text(runtime.model) + '\n'
                            'directory: ' + display_text(runtime.workspace) + '\n'
                            'context: ' + display_text(context_text(runtime.context)) + '\n'
                            'ChatGPT plan · session ' + runtime.session_id[:8]),
                        title='Python Harness', border_style='cyan'))
    show_context_warnings(console, runtime.context)
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
            history=InMemoryHistory(history_strings=runtime.prompts()),
            completer=WordCompleter(lambda: COMMANDS + ['/skill:' + name for name in runtime.context['skills']],
                                    sentence=True),
            complete_while_typing=False, key_bindings=bindings,
        )

    while True:
        try:
            prompt = prompt_session.prompt('› ', bottom_toolbar=lambda: [('',
                display_text(runtime.model) + ' · ' + runtime.session_id[:8] + ' · ' + usage_text(runtime.last_usage))])
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        if not prompt.strip():
            continue
        if prompt.startswith('/skill:'):
            name, request = re.match(r'/skill:(\S*)\s*(.*)', prompt.strip(), re.S).groups()
            try:
                prompt = runtime.skill_prompt(name, request)
            except HarnessError as error:
                console.print(Text(display_text(str(error)), style='yellow'))
                continue
        elif prompt.startswith('/'):
            command, _, argument = prompt.strip().partition(' ')
            argument = argument.strip()
            if command == '/exit':
                break
            if command == '/help':
                console.print(Text('/help  指令說明\n/model [slug]  列出或切換模型\n'
                                   '/new  開新對話，保留舊紀錄並重新載入 AGENTS.md 與 skills\n'
                                   '/skill:名稱 [需求]  套用指定 skill\n/exit  離開'))
                for skill in runtime.context['skills'].values():
                    console.print(Text('  /skill:' + skill['name'] + '  ' + display_text(skill['description']),
                                       style='dim'))
            elif command == '/model':
                if not argument:
                    for item in runtime.catalog['models']:
                        if item.get('visibility') == 'list':
                            console.print(Text(('• ' if item['slug'] == runtime.model else '  ') + display_text(item['slug'])))
                else:
                    try:
                        runtime.set_model(argument)
                    except HarnessError as error:
                        console.print(Text(display_text(str(error)), style='yellow'))
                        continue
                    console.print(Text('模型已切換：' + display_text(runtime.model)))
            elif command == '/new':
                runtime.new_session()
                console.print(Text('已開啟新對話：' + runtime.session_id[:8] + ' · '
                                   + display_text(context_text(runtime.context)), style='cyan'))
                show_context_warnings(console, runtime.context)
            else:
                console.print('未知指令，請輸入 /help。', style='yellow')
            continue

        turn_state = 'pending'
        try:
            with StreamingMarkdown(console) as writer:
                turn_state = 'running'
                runtime.run_turn(prompt, writer, notify=writer.notify,
                                 approve=lambda name, preview: writer.approve(name, preview, prompt_session))
                turn_state = 'saved'
        except (KeyboardInterrupt, httpx.HTTPError, HarnessError) as error:
            if turn_state == 'saved':
                message = '本輪已完成並儲存；終端顯示已中斷。'
            elif isinstance(error, KeyboardInterrupt):
                message = '已停止本次生成，本輪未儲存。'
            elif isinstance(error, httpx.HTTPError):
                message = '連線失敗或逾時，本輪未儲存；可重新輸入。'
            else:
                message = display_text(str(error)) + ' 本輪未儲存。'
            console.print(Text('\n' + message, style='yellow'))
            continue
        finally:
            if turn_state == 'running' and runtime.side_effects:
                console.print('已執行的工具操作不會自動復原；再次嘗試前請檢查檔案與命令結果。', style='yellow')
        console.print(Text(usage_text(runtime.last_usage), style='dim'))
        console.print()
