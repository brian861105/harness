"""Bounded child-process execution and approved project Bash commands."""

import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from ..errors import HarnessError
from .limits import OUTPUT_LIMIT, PROCESS_TIMEOUT


def run_process(argv, workspace):
    # Keep provider credentials and shell startup hooks out of child environments.
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in os.environ}
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    reason = None
    with subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=environment, start_new_session=True) as process:
        terminated = False

        def terminate():
            nonlocal terminated
            if terminated:
                return
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            terminated = True

        try:
            deadline = time.monotonic() + PROCESS_TIMEOUT
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                while selector.get_map() or process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = 'timeout'
                        terminate()
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = OUTPUT_LIMIT - sum(map(len, buffers.values()))
                        buffers[key.data].extend(chunk[:room])
                        if len(chunk) > room:
                            reason = 'output_limit'
                            terminate()
                            break
                    if reason:
                        break
        except BaseException:
            terminate()
            raise
        finally:
            # A shell can exit while redirected background children still run.
            terminate()
            process.wait()
    return {'ok': process.returncode == 0 and reason is None,
            'stdout': buffers['stdout'].decode('utf-8', errors='replace'),
            'stderr': buffers['stderr'].decode('utf-8', errors='replace'),
            'exit_code': process.returncode, 'reason': reason}


class ShellTool:
    def __init__(self, workspace, approve=None):
        self.workspace = Path(workspace).resolve()
        self.approve = approve or (lambda name, preview: False)
        self.side_effects = False

    def run_shell(self, command):
        if not isinstance(command, str) or not command.strip() or '\0' in command or len(command) > 16384:
            raise HarnessError('command 必須是非空的 Bash 命令，且不超過 16384 字元。')
        if not self.approve('run_shell', str(self.workspace) + '\n' + command):
            return {'ok': False, 'error': '使用者拒絕執行；請勿重複提出相同操作。'}
        self.side_effects = True
        return run_process(['/bin/bash', '-c', command], self.workspace)
