"""Persistent stdio app-server client for user-initiated pet conversations."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import uuid
from codex_data import BASE, codex_binary

IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}

def user_input(text, attachments):
    images, files = [], []
    for name in attachments:
        path = Path(name).resolve()
        if not path.is_file():
            raise FileNotFoundError('附件已不存在：'+path.name)
        if path.suffix.lower() in IMAGE_TYPES:
            images.append({'type': 'localImage', 'path': str(path)})
        else:
            files.append(str(path))
    text = text.strip() or '请查看我添加的文件。'
    if files:
        text += '\n\n用户为这条消息添加的本地文件，请根据需要读取：\n'+'\n'.join(files)
    return [{'type': 'text', 'text': text}]+images

class ChatClient:
    def __init__(self, thread_id=None, cwd=None, ephemeral=False):
        self.events = queue.Queue()
        self.thread_id, self.turn_id = thread_id, None
        self.cwd = str(Path(cwd).resolve()) if cwd else str(BASE/'work'/'quick-chat'/uuid.uuid4().hex[:12])
        self.ephemeral = ephemeral
        self.process = None
        self.busy = False
        self.loaded = False
        self.closing = False
        self.cancel_requested = False
        self.interrupting_id = None
        self.control_lock = threading.Lock()
        self.counter = 0
        self.write_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}

    def write(self, message):
        with self.write_lock:
            if not self.process or self.process.poll() is not None:
                raise ConnectionError('Codex 对话连接已关闭')
            self.process.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            self.process.stdin.flush()

    def request(self, method, params=None, timeout=60):
        with self.pending_lock:
            self.counter += 1
            rid = self.counter
            response = queue.Queue()
            self.pending[rid] = response
        try:
            self.write({'id': rid, 'method': method, 'params': params or {}})
            try:
                message = response.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError('Codex 响应超时：'+method)
            if 'error' in message:
                raise RuntimeError(message['error'].get('message', 'Codex 请求失败'))
            return message.get('result', {})
        finally:
            with self.pending_lock:
                self.pending.pop(rid, None)

    def connect(self):
        if self.process and self.process.poll() is None:
            return
        self.loaded = False
        self.closing = False
        self.process = subprocess.Popen([codex_binary(), 'app-server'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=0x08000000)
        process = self.process
        threading.Thread(target=self.read_loop, args=(process,), daemon=True).start()
        self.request('initialize', {'clientInfo': {'name': 'codex_pet_chat', 'title': 'Codex Pet Chat', 'version': '1.0.0'},
            'capabilities': {'experimentalApi': True}}, timeout=30)
        self.write({'method': 'initialized', 'params': {}})

    def read_loop(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'method' in message:
                    if 'id' in message:
                        self.events.put(('request', message))
                    else:
                        self.notification(message['method'], message.get('params') or {})
                elif 'id' in message:
                    with self.pending_lock:
                        pending = self.pending.get(message['id'])
                    if pending:
                        pending.put(message)
        finally:
            with self.pending_lock:
                for pending in self.pending.values():
                    pending.put({'error': {'message': 'Codex 对话连接已关闭'}})
            if not self.closing and self.process is process:
                self.busy, self.loaded = False, False
                self.events.put(('error', 'Codex 连接已断开，消息没有自动重发。'))

    def notification(self, method, params):
        if self.thread_id and params.get('threadId') and params['threadId'] != self.thread_id:
            return
        if method == 'turn/started':
            self.turn_id = (params.get('turn') or {}).get('id')
            self.events.put(('status', '正在回复…'))
            if self.cancel_requested:
                self.interrupt()
        elif method == 'item/agentMessage/delta':
            self.events.put(('delta', {'id': params.get('itemId'), 'text': params.get('delta', '')}))
        elif method in ('item/started', 'item/completed'):
            item = params.get('item') or {}
            kind = item.get('type')
            if kind == 'agentMessage':
                self.events.put(('message', {'id': item.get('id'), 'text': item.get('text', ''), 'phase': item.get('phase')}))
            elif method == 'item/started' and kind in ('commandExecution', 'mcpToolCall', 'fileChange', 'webSearch'):
                self.events.put(('status', {'commandExecution': '正在运行命令…', 'mcpToolCall': '正在使用工具…', 'fileChange': '正在编辑文件…', 'webSearch': '正在搜索…'}[kind]))
        elif method == 'turn/completed':
            turn = params.get('turn') or {}
            self.busy, self.turn_id = False, None
            self.events.put(('done', turn))
        elif method == 'error':
            error = params.get('error') or {}
            text = error.get('message', '对话遇到错误')
            self.events.put(('status' if params.get('willRetry') else 'warning', text))

    def send(self, text, attachments=()):
        if self.busy:
            return False
        inputs = user_input(text, attachments)
        self.busy, self.cancel_requested = True, False
        self.turn_id, self.interrupting_id = None, None
        def run():
            try:
                self.events.put(('status', '正在连接 Codex…'))
                self.connect()
                if not self.loaded:
                    Path(self.cwd).mkdir(parents=True, exist_ok=True)
                    if self.thread_id:
                        result = self.request('thread/resume', {'threadId': self.thread_id, 'excludeTurns': True, 'cwd': self.cwd}, timeout=90)
                    else:
                        params = {'cwd': self.cwd}
                        if self.ephemeral:
                            params['ephemeral'] = True
                        result = self.request('thread/start', params, timeout=90)
                    self.thread_id = result['thread']['id']
                    self.loaded = True
                    self.events.put(('thread', {'id': self.thread_id, 'cwd': self.cwd}))
                if self.cancel_requested:
                    self.busy = False
                    self.events.put(('done', {'status': 'interrupted'}))
                    return
                result = self.request('turn/start', {'threadId': self.thread_id, 'input': inputs}, timeout=90)
                if self.busy:
                    self.turn_id = (result.get('turn') or {}).get('id') or self.turn_id
                if self.cancel_requested:
                    self.interrupt()
            except Exception as error:
                self.busy = False
                self.events.put(('error', str(error)))
        threading.Thread(target=run, daemon=True).start()
        return True

    def answer(self, request_id, result=None, error=None):
        self.write({'id': request_id, **({'error': error} if error else {'result': result or {}})})

    def interrupt(self):
        self.cancel_requested = True
        with self.control_lock:
            turn_id = self.turn_id
            should_interrupt = self.thread_id and turn_id and self.interrupting_id != turn_id
            if should_interrupt:
                self.interrupting_id = turn_id
        if should_interrupt:
            def run():
                try:
                    self.request('turn/interrupt', {'threadId': self.thread_id, 'turnId': turn_id}, timeout=15)
                except Exception as error:
                    self.events.put(('warning', '停止请求未确认：'+str(error)))
            threading.Thread(target=run, daemon=True).start()

    def reset(self):
        if self.busy:
            return False
        self.thread_id, self.turn_id, self.loaded = None, None, False
        self.cwd = str(BASE/'work'/'quick-chat'/uuid.uuid4().hex[:12])
        return True

    def close(self):
        self.closing = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
