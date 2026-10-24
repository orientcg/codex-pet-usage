"""Read-only companion for the built-in Codex pet. Python 3.10+, stdlib only."""
import ctypes
import json
import re
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from quota_estimate import rate_limit_sample, estimate_remaining_requests
from usage_series import today_hour_buckets

class RollingUsage:
    """Incremental local 24-hour totals, deduplicated by response ID."""
    def __init__(self):
        self.positions = {}
        self.records = {}
        self.quota_samples = {}
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return self._read()

    def _read(self):
        now = time.time()
        cutoff = now-86400
        paths = list((HOME/'sessions').glob('**/*.jsonl'))+list((HOME/'archived_sessions').glob('**/*.jsonl'))
        for p in paths:
            stat = p.stat()
            offset = self.positions.get(str(p), 0)
            if offset > stat.st_size:
                offset = 0
            if offset == stat.st_size:
                continue
            with p.open('rb') as f:
                f.seek(offset)
                while True:
                    start = f.tell()
                    line = f.readline()
                    if not line or not line.endswith(b'\n'):
                        self.positions[str(p)] = start
                        break
                    if b'"token_usage_record"' not in line and b'"token_count"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                        sample = rate_limit_sample(r)
                        if sample and cutoff <= sample['timestamp'] <= now:
                            key = (sample['limit_id'], sample['resets_at'], sample['timestamp'], sample['used_percent'])
                            self.quota_samples[key] = sample
                        if r.get('type') != 'token_usage_record':
                            continue
                        stamp = datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00')).timestamp()
                        pay = r['payload']
                        rid = pay.get('response_id')
                        total = (pay.get('usage') or {}).get('total_tokens')
                        if rid and total is not None and stamp >= cutoff:
                            self.records[rid] = (stamp, total)
                    except (ValueError, KeyError, TypeError):
                        pass
        self.records = {k:v for k,v in self.records.items() if cutoff <= v[0] <= now}
        self.quota_samples = {k:v for k,v in self.quota_samples.items() if cutoff <= v['timestamp'] <= now}
        bins = [0]*24
        for stamp, total in self.records.values():
            bins[min(23, int((stamp-cutoff)//3600))] += total
        return [{'startDate': datetime.fromtimestamp(cutoff+i*3600).strftime('%H:%M'), 'tokens': n} for i,n in enumerate(bins)]

    def estimate_requests(self, limits, limit_id):
        with self.lock:
            return estimate_remaining_requests(self.records, self.quota_samples.values(), limits, limit_id)

    def today_usage(self):
        with self.lock:
            return today_hour_buckets(self.records)

BASE = Path(__file__).resolve().parent
HOME = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
BG, CARD, FG, MUTED, ACCENT = '#171923', '#232636', '#eeeeff', '#a4acc2', '#bdacff'

def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default

def codex_binary():
    root = Path(os.environ['LOCALAPPDATA']) / 'OpenAI/Codex/bin'
    files = list(root.glob('*/codex.exe'))
    if not files:
        raise RuntimeError('未找到 Codex 程序，请先安装或启动 Codex。')
    return str(max(files, key=lambda p: p.stat().st_mtime))

def fetch_account():
    p = subprocess.Popen([codex_binary(), 'app-server'], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding='utf-8', creationflags=0x08000000)
    messages = queue.Queue()
    def reader():
        for line in p.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
        messages.put({'error': {'message': 'Codex 用量连接已关闭'}})
    threading.Thread(target=reader, daemon=True).start()
    def send(msg):
        p.stdin.write(json.dumps(msg) + '\n')
        p.stdin.flush()
    def response(request_id):
        end = time.monotonic() + 25
        while time.monotonic() < end:
            msg = messages.get(timeout=max(.01, end-time.monotonic()))
            if msg.get('id') == request_id or ('error' in msg and 'id' not in msg):
                if 'error' in msg:
                    raise RuntimeError(msg['error'].get('message', '读取失败'))
                return msg['result']
        raise TimeoutError('读取额度超时')
    try:
        send({'id': 0, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'codex_pet_usage', 'version': '1.0.0'}}})
        response(0)
        send({'method': 'initialized', 'params': {}})
        send({'id': 1, 'method': 'account/rateLimits/read'})
        limits = response(1)
        send({'id': 2, 'method': 'account/usage/read'})
        try:
            usage = response(2)
        except Exception:
            usage = None
        return limits, usage
    finally:
        p.terminate()
        try:
            p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()

def session_names():
    result = {}
    try:
        with (HOME / 'session_index.jsonl').open(encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    result[r['id']] = r.get('thread_name', r['id'])
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return result

def scan_sessions():
    # Only active logs; the companion never writes to Codex databases or logs.
    files = list((HOME / 'sessions').glob('**/*.jsonl'))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

def latest_usage(path):
    # Search from the end, handling logs whose last record is still being written.
    with path.open('rb') as f:
        end = f.seek(0, 2)
        data = b''
        while end > 0:
            size = min(end, 262144)
            end -= size
            f.seek(end)
            data = f.read(size) + data
            lines = data.splitlines()
            for line in reversed(lines if end == 0 else lines[1:]):
                try:
                    r = json.loads(line)
                    payload = r.get('payload', {})
                    if r.get('type') == 'event_msg' and payload.get('type') == 'token_count' and payload.get('info'):
                        return payload['info'], r.get('timestamp')
                except (ValueError, UnicodeError):
                    pass
            if end:
                data = lines[0] if lines else data
    return None, None

def last_request_usage(snapshots):
    """Pick the newest recorded request, independently of later chat activity."""
    latest = None
    for path, snapshot in snapshots.items():
        usage = ((snapshot.get('info') or {}).get('last_token_usage') or {})
        tokens = usage.get('total_tokens')
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            continue
        try:
            stamp = datetime.fromisoformat(snapshot['usage_time'].replace('Z', '+00:00')).timestamp()
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
            continue
        if latest is None or stamp > latest['timestamp']:
            latest = {'tokens': tokens, 'timestamp': stamp, 'path': str(path)}
    return latest


def window_label(minutes):
    if minutes is None:
        return '额度窗口'
    if minutes >= 1440:
        return f'{minutes / 1440:g} 天窗口'
    return f'{minutes / 60:g} 小时窗口' if minutes >= 60 else f'{minutes:g} 分钟窗口'

def fetch_limits():
    return fetch_account()[0]

def session_snapshot(path):
    """Read a bounded tail; unknown activity is reported as unknown, never guessed."""
    with path.open('rb') as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - 2*1024*1024))
        lines = f.read().splitlines()
    info, usage_time, state, summary = None, None, 'unknown', ''
    active_time, summary_time = None, None
    last_record_time = None
    for line in reversed(lines):
        # Windows may defer LastWriteTime until the writer closes its handle.
        # Activity is therefore timed from the records, not that file property.
        if last_record_time is None:
            match = re.search(br'"timestamp"\s*:\s*"([^"\r\n]+)"', line[:200])
            if match:
                try:
                    last_record_time = datetime.fromisoformat(match[1].decode().replace('Z', '+00:00')).timestamp()
                except (ValueError, UnicodeError):
                    pass
        # Ignore large image/tool payloads, and partially written lines.
        if not any(term in line for term in (b'"token_count"', b'"task_started"', b'"task_complete"', b'"turn_aborted"', b'"agent_message"', b'"role":"assistant"', b'"role": "assistant"')):
            continue
        try:
            r = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        pay = r.get('payload', {})
        if r.get('type') == 'event_msg':
            typ = pay.get('type')
            if typ == 'token_count' and info is None and pay.get('info'):
                info, usage_time = pay['info'], r.get('timestamp')
            elif typ in ('task_started', 'task_complete', 'turn_aborted') and state == 'unknown':
                state = {'task_started': 'running', 'task_complete': 'ready', 'turn_aborted': 'stopped'}[typ]
                active_time = r.get('timestamp')
            elif typ == 'agent_message' and not summary:
                summary = pay.get('message', '')
                summary_time = r.get('timestamp')
        elif r.get('type') == 'response_item' and pay.get('role') == 'assistant' and not summary:
            if pay.get('channel') != 'analysis':
                summary = ' '.join(x.get('text', '') for x in pay.get('content', []) if isinstance(x, dict))
                summary_time = r.get('timestamp')
        if info is not None and state != 'unknown' and summary:
            break
    if last_record_time is None:
        last_record_time = path.stat().st_mtime
    if state == 'running' and time.time() - last_record_time > 900:
        state = 'unknown'
    if info is None:
        info, usage_time = latest_usage(path)
    return {'info': info, 'usage_time': usage_time, 'state': state,
            'summary': ' '.join(summary.split())[:140], 'summary_time': summary_time,
            'active_time': active_time, 'last_record_time': last_record_time}

