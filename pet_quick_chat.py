"""Compact local composer and streaming reply view for the desktop pet."""
import ctypes
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import queue
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLineEdit, QLabel, QTextBrowser, QFileDialog, QMenu, QMessageBox, QInputDialog, QApplication
from codex_data import BASE, read_json
from pet_chat import ChatClient

def ui_font(size=10):
    return QFont('Microsoft YaHei UI', size)

class SendButton(QPushButton):
    def __init__(self, callback):
        super().__init__()
        self.setFixedSize(29, 29)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.busy = False
        self.clicked.connect(callback)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#83b3fa' if self.isEnabled() else '#c4dafa'))
        p.drawEllipse(1, 1, 27, 27)
        p.setPen(QPen(QColor('#ffffff'), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self.busy:
            p.setBrush(QColor('#ffffff'))
            p.drawRoundedRect(10, 10, 9, 9, 1, 1)
        else:
            p.drawLine(14, 21, 14, 9)
            p.drawLine(9, 14, 14, 9)
            p.drawLine(14, 9, 19, 14)

class QuickChat(QWidget):
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setWindowTitle('Codex Pet · 开始新聊天')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(370)
        state = read_json(BASE/'quick-chat.json', {})
        self.history = state.get('messages', [])
        self.attachments = state.get('attachments', [])
        self.client = ChatClient(state.get('threadId'), state.get('cwd'))
        self.handling_request = False
        self.interaction_depth = 0
        self.mouse_buttons = 0
        self.requests = []
        self.dirty = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 8)
        layout.setSpacing(7)
        self.input_frame = QFrame()
        self.input_frame.setObjectName('quickInput')
        self.input_frame.setFixedHeight(48)
        self.input_frame.setStyleSheet('QFrame#quickInput{background:rgba(255,255,255,250);border:1px solid #e9e9ee;border-radius:24px;}')
        row = QHBoxLayout(self.input_frame)
        row.setContentsMargins(9, 5, 10, 5)
        row.setSpacing(8)
        self.plus = QPushButton('＋')
        self.plus.setFixedSize(30, 30)
        self.plus.setFont(ui_font(17))
        self.plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus.setStyleSheet('QPushButton{background:#f3f4f6;color:#68717b;border:0;border-radius:15px;}QPushButton:hover{background:#e9eff7;}')
        self.plus.clicked.connect(self.plus_menu)
        row.addWidget(self.plus)
        self.input = QLineEdit()
        self.input.setFont(ui_font(10))
        self.input.setPlaceholderText('继续聊天' if self.client.thread_id or self.history else '开始新聊天')
        self.input.setStyleSheet('QLineEdit{border:0;background:transparent;color:#515866;padding:0;selection-background-color:#c9ddfc;}')
        palette = self.input.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, QColor('#b8bdc5'))
        self.input.setPalette(palette)
        self.input.setText(state.get('draft', ''))
        self.input.returnPressed.connect(lambda: self.submit() if not self.client.busy else None)
        row.addWidget(self.input, 1)
        self.send = SendButton(self.submit)
        row.addWidget(self.send)
        layout.addWidget(self.input_frame)
        self.attachment_label = QLabel()
        self.attachment_label.setFont(ui_font(8))
        self.attachment_label.setStyleSheet('color:#8b92a0;background:transparent;padding:0 10px;')
        self.attachment_label.hide()
        layout.addWidget(self.attachment_label)
        self.reply = QFrame()
        self.reply.setObjectName('quickReply')
        self.reply.setStyleSheet('QFrame#quickReply{background:#fcfaff;border:1px solid #e7e1ee;border-radius:18px;}')
        rv = QVBoxLayout(self.reply)
        rv.setContentsMargins(12, 10, 12, 10)
        rv.setSpacing(6)
        header = QHBoxLayout()
        self.status = QLabel('桌宠聊天')
        self.status.setFont(ui_font(9))
        self.status.setStyleSheet('color:#8f819d;background:transparent;')
        header.addWidget(self.status, 1)
        self.new_button = QPushButton('新聊天')
        self.new_button.setFont(ui_font(8))
        self.new_button.setStyleSheet('border:0;background:transparent;color:#9a8aa8;')
        self.new_button.clicked.connect(self.start_new)
        header.addWidget(self.new_button)
        rv.addLayout(header)
        self.transcript = QTextBrowser()
        self.transcript.setFont(ui_font(10))
        self.transcript.setMinimumHeight(190)
        self.transcript.setMaximumHeight(260)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setStyleSheet('QTextBrowser{border:0;background:transparent;color:#574e61;selection-background-color:#e6dcf2;}QScrollBar:vertical{width:5px;background:transparent;}QScrollBar::handle:vertical{background:#dfd5e8;border-radius:2px;}')
        rv.addWidget(self.transcript)
        layout.addWidget(self.reply)
        self.reply.setVisible(bool(self.history))
        self.input.textChanged.connect(self.sync_send)
        self.input.textEdited.connect(lambda _: self.save())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(70)
        # Qt sees clicks in our windows; the native sampler also sees a click
        # on the desktop or another app without consuming that click.
        QApplication.instance().installEventFilter(self)
        self.outside_timer = QTimer(self)
        self.outside_timer.setInterval(16)
        self.outside_timer.timeout.connect(self.poll_outside_click)
        self.refresh_attachments()
        self.render_messages()
        self.sync_send()
        self.adjustSize()

    def eventFilter(self, source, event):
        # This application-wide filter also blocks native/generated hover hints.
        if event.type() == QEvent.Type.ToolTip:
            return True
        if self.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress:
                self.dismiss_if_outside(event.globalPosition().toPoint())
            elif (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
                  and isinstance(source, QWidget) and (source is self or self.isAncestorOf(source))
                  and not self.interaction_active()):
                self.dismiss()
                return True
        return super().eventFilter(source, event)

    @staticmethod
    def read_mouse_buttons():
        if os.name != 'nt':
            return 0
        return sum(1 << i for i, key in enumerate((0x01, 0x02, 0x04, 0x05, 0x06))
                   if ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)

    def interaction_active(self):
        return (self.interaction_depth > 0 or self.handling_request
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None)

    def dismiss_if_outside(self, position):
        if self.isVisible() and not self.interaction_active() and not self.frameGeometry().contains(position):
            self.dismiss()

    def poll_outside_click(self):
        buttons = self.read_mouse_buttons()
        pressed = buttons & ~self.mouse_buttons
        self.mouse_buttons = buttons
        if pressed:
            self.dismiss_if_outside(QCursor.pos())

    @contextmanager
    def local_interaction(self):
        # Menus and native file dialogs belong to the chat even outside its
        # rectangle. Their opening/closing click must not dismiss the chat.
        self.interaction_depth += 1
        try:
            yield
        finally:
            self.interaction_depth -= 1
            self.mouse_buttons = self.read_mouse_buttons()

    def reveal(self):
        self.owner.panel.dismiss()
        self.show()
        self.owner.toolbar.hide()
        self.owner.layout_stack(animated=False)
        self.position_in_stack()
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.MouseFocusReason)
        # Ignore the mouse press that opened this window.
        self.mouse_buttons = self.read_mouse_buttons()
        self.outside_timer.start()

    def position_in_stack(self):
        screen = self.owner.screen().availableGeometry()
        x = self.owner.x()+self.owner.width()//2-self.width()//2
        y = self.owner.toolbar.mapToGlobal(self.owner.toolbar.rect().topLeft()).y()+2
        self.move(max(screen.left(), min(x, screen.right()-self.width()+1)), y)

    def voice_input(self):
        self.input.setFocus(Qt.FocusReason.MouseFocusReason)
        QTimer.singleShot(180, self.start_dictation)

    def start_dictation(self):
        if not self.isVisible() or not self.input.hasFocus() or os.name != 'nt':
            return
        # Open the user's Windows voice typing in the focused chat input.
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)
        try:
            user32.keybd_event(0x48, 0, 0, 0)
            user32.keybd_event(0x48, 0, 2, 0)
        finally:
            user32.keybd_event(0x5B, 0, 2, 0)

    def fit_screen_height(self):
        screen_height = self.owner.screen().availableGeometry().height()
        summary_height = self.owner.speech.height()+8 if self.owner.speech.isVisible() else 0
        self.transcript.setFixedHeight(max(70, min(220, screen_height-round(self.owner.pet_size*208/192)-summary_height-150)))
        self.adjustSize()

    def resize_to_content(self):
        self.fit_screen_height()
        if self.isVisible():
            self.owner.layout_stack(animated=False)
            self.position_in_stack()

    def dismiss(self):
        self.outside_timer.stop()
        self.save()
        self.hide()
        self.owner.toolbar.show()
        self.owner.layout_stack(animated=False)

    def closeEvent(self, event):
        self.dismiss()
        event.ignore()

    def plus_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet('QMenu{background:white;border:1px solid #e6e7ec;border-radius:9px;padding:5px;color:#68717c;}QMenu::item{padding:7px 14px;}QMenu::item:selected{background:#eef3fa;}')
        add = menu.addAction('添加图片或文件', self.add_files)
        add.setEnabled(not self.client.busy)
        if self.attachments:
            attached = menu.addMenu('移除附件')
            for path in self.attachments:
                action = attached.addAction(Path(path).name)
                action.triggered.connect(lambda checked=False, p=path: self.remove_attachment(p))
        menu.addSeparator()
        menu.addAction('开始新聊天', self.start_new).setEnabled(not self.client.busy)
        with self.local_interaction():
            menu.exec(self.plus.mapToGlobal(self.plus.rect().bottomLeft()))
        if self.isVisible():
            self.input.setFocus()

    def add_files(self):
        with self.local_interaction():
            paths, _ = QFileDialog.getOpenFileNames(self, '添加图片或文件', '', '所有文件 (*);;图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)')
        self.attachments.extend(p for p in paths if p not in self.attachments)
        self.refresh_attachments()
        self.save()

    def remove_attachment(self, path):
        self.attachments = [p for p in self.attachments if p != path]
        self.refresh_attachments()
        self.save()

    def refresh_attachments(self):
        self.attachment_label.setText(f'已添加 {len(self.attachments)} 项 · 在＋中管理' if self.attachments else '')
        self.attachment_label.setVisible(bool(self.attachments))
        self.sync_send()
        self.resize_to_content()

    def sync_send(self):
        self.send.busy = self.client.busy
        self.send.setEnabled(self.client.busy or bool(self.input.text().strip() or self.attachments))
        self.send.update()
        self.new_button.setEnabled(not self.client.busy)

    def submit(self):
        if self.client.busy:
            self.client.interrupt()
            self.status.setText('正在停止…')
            return
        text = self.input.text().strip()
        if not text and not self.attachments:
            return
        try:
            accepted = self.client.send(text, self.attachments)
        except Exception as error:
            self.show_error(str(error))
            return
        if not accepted:
            return
        display = text
        if self.attachments:
            display += '\n'+''.join('\n📎 '+Path(p).name for p in self.attachments)
        self.history.append({'role': 'user', 'text': display.strip()})
        self.input.clear()
        self.input.setPlaceholderText('继续聊天')
        self.attachments = []
        self.reply.show()
        self.refresh_attachments()
        self.render_messages()
        self.sync_send()
        self.resize_to_content()
        self.save()

    def assistant_message(self, item_id):
        for message in self.history:
            if message.get('role') == 'assistant' and message.get('id') == item_id:
                return message
        message = {'role': 'assistant', 'id': item_id, 'text': ''}
        self.history.append(message)
        return message

    def render_messages(self):
        scroll = self.transcript.verticalScrollBar()
        at_end = scroll.value() >= scroll.maximum()-15
        chunks = []
        for message in self.history:
            if not message.get('text'):
                continue
            who = '你' if message.get('role') == 'user' else '桌宠' if message.get('role') == 'assistant' else '提示'
            text = html.escape(message['text']).replace('\n', '<br>')
            chunks.append(f'<p style="margin-top:10px;margin-bottom:4px;color:#a092ad;font-size:9pt">{who}</p><p style="margin-top:0;color:#564e60;font-size:10pt">{text}</p>')
        old = scroll.value()
        self.transcript.setHtml(''.join(chunks))
        scroll.setValue(scroll.maximum() if at_end else old)

    def show_error(self, text):
        self.reply.show()
        self.status.setText('未完成')
        self.history.append({'role': 'notice', 'text': text})
        self.render_messages()
        self.resize_to_content()
        self.save()

    def poll(self):
        changed = False
        try:
            while True:
                kind, data = self.client.events.get_nowait()
                if kind == 'status':
                    self.status.setText(data)
                elif kind in ('delta', 'message'):
                    message = self.assistant_message(data.get('id'))
                    message['text'] = message.get('text', '')+data['text'] if kind == 'delta' else data['text']
                    changed = True
                elif kind == 'thread':
                    self.save()
                elif kind == 'done':
                    status = data.get('status', 'completed')
                    self.status.setText('已停止' if status == 'interrupted' else '回复完成' if status == 'completed' else '未完成')
                    error = data.get('error') or {}
                    if error.get('message'):
                        self.history.append({'role': 'notice', 'text': error['message']})
                    changed = True
                    self.save()
                elif kind in ('error', 'warning'):
                    self.show_error(data)
                elif kind == 'request':
                    self.requests.append(data)
        except queue.Empty:
            pass
        if changed:
            self.render_messages()
        self.sync_send()
        # A collapsed chat stays collapsed. Resume pending questions only when
        # the user explicitly opens it again.
        if self.requests and not self.handling_request and self.isVisible():
            self.handle_request(self.requests.pop(0))

    def handle_request(self, request):
        self.handling_request = True
        self.status.setText('需要你的答复')
        method, params = request['method'], request.get('params') or {}
        try:
            if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval', 'item/permissions/requestApproval'):
                detail = params.get('reason') or params.get('command') or params.get('changes') or params.get('permissions') or params
                if not isinstance(detail, str):
                    detail = json.dumps(detail, ensure_ascii=False, indent=2)
                box = QMessageBox(QMessageBox.Icon.Question, '桌宠聊天 · 操作请求', '是否允许这次操作？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
                box.setDetailedText(detail)
                accepted = box.exec() == QMessageBox.StandardButton.Yes
                result = {'permissions': params.get('permissions', {}) if accepted else {}, 'scope': 'turn'} if method.endswith('permissions/requestApproval') else {'decision': 'accept' if accepted else 'decline'}
                self.client.answer(request['id'], result)
            elif method == 'item/tool/requestUserInput':
                answers = {}
                for question in params.get('questions', []):
                    prompt = question.get('question', '')
                    for option in question.get('options') or []:
                        prompt += '\n'+option.get('label', '')+'：'+option.get('description', '')
                    value, ok = QInputDialog.getText(self, question.get('header', '请回答'), prompt)
                    answers[question['id']] = {'answers': [value] if ok else []}
                self.client.answer(request['id'], {'answers': answers})
            else:
                self.client.answer(request['id'], error={'code': -32601, 'message': '桌宠输入条暂不支持这类原生交互请求：'+method})
                self.show_error('这条请求需要原生客户端能力：'+method)
        except Exception as error:
            self.show_error(str(error))
        finally:
            self.handling_request = False
            self.mouse_buttons = self.read_mouse_buttons()

    def start_new(self):
        if not self.client.reset():
            return
        self.history, self.attachments = [], []
        self.input.clear()
        self.input.setPlaceholderText('开始新聊天')
        self.status.setText('桌宠聊天')
        self.reply.hide()
        self.refresh_attachments()
        self.render_messages()
        self.save()
        self.input.setFocus()

    def save(self):
        try:
            state = {'threadId': self.client.thread_id, 'cwd': self.client.cwd, 'draft': self.input.text(), 'attachments': self.attachments, 'messages': self.history}
            target = BASE/'quick-chat.json'
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            temp.replace(target)
        except OSError:
            pass

    def shutdown(self):
        self.outside_timer.stop()
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        self.save()
        self.client.close()
        self.hide()
