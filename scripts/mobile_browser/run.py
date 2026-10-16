"""Run disposable loopback-only mobile browser checks with the current Python."""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler
import uuid

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def require_free_port(port: int) -> None:
    # Never attach to a previous fixture or an unrelated service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as error:
            raise RuntimeError(f'127.0.0.1:{port} is occupied; no process was started.') from error


def start(script: str, env: dict[str, str], **kwargs) -> subprocess.Popen:
    group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.Popen([sys.executable, str(HERE / script)], cwd=ROOT, env=env, **group, **kwargs)


def stop_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        # Own the exact Python process and its children; never start cmd.exe.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)


def wait_ready(process: subprocess.Popen, origin: str, run_id: str, output: Path) -> None:
    deadline = time.monotonic() + 45
    local_http = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Fixture exited; inspect {output / "fixture-server.log"}')
        try:
            with local_http.open(origin + '/__mobile_fixture_ready', timeout=1) as response:
                ready = json.load(response)
            if ready.get('run_id') == run_id:
                meta = json.loads((output / 'fixture.json').read_text(encoding='utf-8'))
                if meta.get('run_id') == run_id:
                    return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError(f'Fixture startup timed out; inspect {output / "fixture-server.log"}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9888, help='Loopback port (default: 9888).')
    parser.add_argument('--suite', choices=('all', 'proxy', 'bots', 'availability', 'activity', 'schedule', 'profile'), default='all', help='Full mobile checks or focused business workflows.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    require_free_port(args.port)
    for module in ('playwright', 'uvicorn', 'fastapi'):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f'Missing dependency: {module}. See scripts/mobile_browser/README.md.')
    executable = os.environ.get('ROBORT_BROWSER_EXECUTABLE', '').strip()
    if executable and not Path(executable).is_file():
        raise RuntimeError('ROBORT_BROWSER_EXECUTABLE must identify an installed Chromium/Edge executable.')
    run_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    output = ROOT / 'test-results' / 'mobile-browser' / run_id
    output.mkdir(parents=True)
    temporary = output / 'tmp'
    temporary.mkdir()
    inherited = {key:value for key,value in os.environ.items()
                 if not key.startswith('ROBORT_') or key == 'ROBORT_BROWSER_EXECUTABLE'}
    env = dict(inherited, ROBORT_ENV_FILE='-', ROBORT_MOBILE_RUN_ID=run_id, ROBORT_MOBILE_RUN_DIR=str(output),
               ROBORT_MOBILE_SUITE=args.suite, ROBORT_MOBILE_PORT=str(args.port), ROBORT_STATE_DIR=str(output / 'state'),
               ROBORT_LIVE_SEND='0', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    server = browser = None
    print(f'Local browser artifacts: {output}', flush=True)
    with (output / 'fixture-server.log').open('w', encoding='utf-8') as log:
        try:
            server = start('fixture.py', env, stdout=log, stderr=log)
            # Wait for the new service before reading this run's fixture metadata.
            wait_ready(server, f'http://127.0.0.1:{args.port}', run_id, output)
            browser = start({'all':'check.py','proxy':'check_proxy.py','bots':'check_bot_onboarding.py','availability':'check_availability.py','activity':'check_activity.py','schedule':'check_schedule.py','profile':'check_profile.py'}[args.suite], env)
            return browser.wait()
        finally:
            stop_tree(browser)
            stop_tree(server)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyboardInterrupt) as error:
        print(str(error) or 'Browser test interrupted.', file=sys.stderr)
        raise SystemExit(1)
