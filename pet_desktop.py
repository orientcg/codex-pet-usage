"""Qt desktop companion: alpha-transparent pet, polished usage popover."""
import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPoint, QPointF, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, Property, QObject, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPixmap, QImage, QCursor, QPainterPath, QLinearGradient, QFontMetrics
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QListWidget, QListWidgetItem, QMenu, QGraphicsDropShadowEffect, QToolTip, QComboBox, QGraphicsScene, QGraphicsView
from codex_data import BASE, HOME, read_json, fetch_account, scan_sessions, session_snapshot, session_names, last_request_usage, RollingUsage
from pet_toolbar import Toolbar
from usage_series import fixed_usage_series, local_now

INK = '#37313f'
MUTED = '#96909f'
PURPLE = '#8e78a9'
FRAME_COUNTS = [6, 8, 8, 4, 5, 8, 6, 6, 6, 8, 8]
STATE_NAMES = {'running': '正在工作', 'ready': '已完成', 'stopped': '已中断', 'unknown': '状态待确认'}
DEFAULT_UI_SCALE = 1.3
_display_scale_configured = False


class NoToolTips(QObject):
    def eventFilter(self, source, event):
        return event.type() == QEvent.Type.ToolTip or super().eventFilter(source, event)


def configure_display_scale():
    """Scale every window and pointer coordinate together before Qt starts."""
    global _display_scale_configured
    if _display_scale_configured:
        return
    try:
        previous = float(os.environ.get('QT_SCALE_FACTOR', '1'))
        if not math.isfinite(previous) or previous <= 0:
            previous = 1.
    except ValueError:
        previous = 1.
    os.environ['QT_SCALE_FACTOR'] = f'{previous*DEFAULT_UI_SCALE:g}'
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _display_scale_configured = True

def clear_panel_rect(available, protected, size, capsule):
    """Find an on-screen position outside the pet, toolbar and progress bubble."""
    gap, w, h = 2, size.width(), size.height()
    anchor_x, anchor_y = capsule.center().x(), capsule.center().y()
    clamp_x = lambda x: max(available.left(), min(x, available.right()-w+1))
    clamp_y = lambda y: max(available.top(), min(y, available.bottom()-h+1))
    bounds = QRect(protected[0])
    for area in protected[1:]:
        bounds = bounds.united(area)
    candidates = [
        QRect(bounds.left()-w-gap, clamp_y(anchor_y-h+22), w, h),
        QRect(bounds.right()+gap+1, clamp_y(anchor_y-h+22), w, h),
        QRect(clamp_x(anchor_x-w//2), bounds.top()-h-gap, w, h),
        QRect(clamp_x(anchor_x-w//2), bounds.bottom()+gap+1, w, h),
    ]
    # Corner positions also handle pets near the centre of a smaller monitor.
    for x in (available.left(), available.right()-w+1):
        for y in (available.top(), available.bottom()-h+1):
            candidates.append(QRect(x, y, w, h))
    valid = [r for r in candidates if available.contains(r) and all(not r.intersects(p) for p in protected)]
    return min(valid, key=lambda r: (r.center()-capsule.center()).manhattanLength()) if valid else None

def short(n):
    if n is None:
        return '—'
    return f'{n/1e6:.2f}M' if n >= 1e6 else f'{n/1000:.1f}K' if n >= 1000 else str(n)

def font(size, weight=QFont.Weight.Normal):
    f = QFont('Microsoft YaHei UI', size)
    f.setWeight(weight)
    return f

def label(text='', size=11, color=INK, weight=QFont.Weight.Normal):
    item = QLabel(text)
    item.setFont(font(size, weight))
    item.setStyleSheet(f'color:{color}; background:transparent; border:none;')
    return item

def pill(text, callback, selected=False):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(font(14))
    b.setFixedHeight(32)
    b.clicked.connect(callback)
    select(b, selected)
    return b

def select(button, selected):
    button.setStyleSheet('QPushButton{border:0;border-radius:9px;padding:3px 8px;color:'+('#645079' if selected else '#92869f')+';background:'+('#eae2f2' if selected else 'transparent')+';}QPushButton:hover{background:#ede7f3;}QPushButton:pressed{background:#e3d8ed;}')

class IconButton(QPushButton):
    def __init__(self, icon, action, tooltip, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(action)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#efe9f5'))
            p.drawRoundedRect(QRectF(1, 1, 32, 32), 10, 10)
        p.setPen(QPen(QColor('#82718f'), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        name = self.icon_name
        if name == 'refresh':
            p.drawArc(QRectF(8, 8, 18, 18), 45*16, 300*16)
            p.drawLine(QPointF(24.5, 6), QPointF(24.5, 12))
            p.drawLine(QPointF(24.5, 12), QPointF(19, 11))
        elif name == 'copy':
            p.drawRoundedRect(QRectF(10, 8, 15, 20), 2.5, 2.5)
            p.setBrush(QColor('#fcfaff'))
            p.drawRoundedRect(QRectF(14, 6, 7, 5), 1.4, 1.4)
        elif name == 'pet':
            p.drawRoundedRect(QRectF(6, 13, 22, 12), 6, 6)
            p.drawLine(14, 8, 20, 8)
            p.drawLine(17, 8, 17, 12)
            p.drawLine(14, 29, 20, 29)
            p.drawPoint(13, 19)
            p.drawPoint(21, 19)
        elif name == 'collapse':
            for y in (10, 17, 24):
                p.drawLine(7, y, 18, y)
            p.drawLine(23, 16, 28, 21)
            p.drawLine(28, 21, 23, 26)
        p.end()

class Mark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 32)
        self.icon = QSvgRenderer(str(BASE/'codex-app.svg'), self)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.icon.render(p, QRectF(0, 1, 30, 30))

class Progress(QWidget):
    def __init__(self):
        super().__init__()
        self.value = None
        self.setFixedHeight(8)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#e7e0ed'))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 7), 3.5, 3.5)
        if self.value is not None and self.value > 0:
            gradient = QLinearGradient(0, 0, self.width(), 0)
            gradient.setColorAt(0, QColor('#8c76a5'))
            gradient.setColorAt(1, QColor('#ad96c2'))
            p.setBrush(QBrush(gradient))
            p.drawRoundedRect(QRectF(0, 0, self.width()*self.value/100, 7), 3.5, 3.5)

class QuotaCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName('quotaCard')
        self.setStyleSheet('QFrame#quotaCard{background:#f5f1f8;border-radius:18px;}')
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 14, 12, 14)
        v.setSpacing(12)
        self.rows = []
        for caption in ('5h剩余', '周剩余'):
            col = QVBoxLayout()
            col.setSpacing(8)
            name = label(caption, 14, '#8f869a')
            col.addWidget(name)
            bar = Progress()
            col.addWidget(bar)
            v.addLayout(col)
            self.rows.append((name, bar))
        v.addStretch(1)
        estimate = QVBoxLayout()
        estimate.setSpacing(3)
        estimate.addWidget(label('5h 预计可用', 12, '#8f869a'))
        self.estimate_value = label('采样中', 18, '#635071', QFont.Weight.Medium)
        estimate.addWidget(self.estimate_value)
        estimate.addWidget(label('按近期请求估算', 10, MUTED))
        v.addLayout(estimate)

    def render(self, limits, estimate=None):
        for key, (name, bar) in zip(('primary', 'secondary'), self.rows):
            item = limits.get(key) or {}
            used = item.get('usedPercent')
            bar.value = max(0, min(100, 100-used)) if used is not None else None
            minutes = item.get('windowDurationMins')
            name.setText('5h剩余' if minutes == 300 else '周剩余' if minutes == 10080 else '剩余额度')
            bar.update()
        reset = (limits.get('primary') or {}).get('resetsAt')
        if estimate is not None and estimate.get('resets_at') != reset:
            estimate = None
        self.estimate_value.setText(f"约 {estimate['count']:,} 次" if estimate is not None else '采样中')

class Chart(QWidget):
    def __init__(self):
        super().__init__()
        self.series = []
        self.loading = True
        self.regions = []
        self.bar_rects = []
        self.setMinimumHeight(102)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(13))
        p.setPen(QColor(MUTED))
        w, h = self.width(), self.height()
        self.regions = []
        self.bar_rects = []
        if not self.series:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '正在读取用量' if self.loading else '此范围暂无记录')
            return
        maximum = max(x['tokens'] for x in self.series) or 1
        magnitude = 10**math.floor(math.log10(maximum))
        top = next(step*magnitude for step in (1, 2, 5, 10) if step*magnitude >= maximum)
        ticks = (top, top/2, 0) if top >= 2 else (top, 0)
        def axis_text(value):
            return f'{value/1e6:g}M' if value >= 1e6 else f'{value/1000:g}K' if value >= 1000 else f'{value:g}'
        axis_width = max(QFontMetrics(p.font()).horizontalAdvance(axis_text(value)) for value in ticks)+10
        plot_top, baseline = 12., float(h-12)
        plot_height = max(1., baseline-plot_top)
        for value in ticks:
            y = baseline-plot_height*value/top
            p.setPen(QColor('#a096ac'))
            p.drawText(QRectF(0, y-11, axis_width-8, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, axis_text(value))
            p.setPen(QPen(QColor('#e9e2ef'), .7))
            p.drawLine(QPointF(axis_width, y), QPointF(w-2, y))
        step = max(1., (w-axis_width-4)/len(self.series))
        gradient = QLinearGradient(0, plot_top, 0, baseline)
        gradient.setColorAt(0, QColor('#9d86b8'))
        gradient.setColorAt(1, QColor('#b9a7cd'))
        p.setPen(Qt.PenStyle.NoPen)
        for i, item in enumerate(self.series):
            left = axis_width+2+i*step+max(.6, step*.12)
            height = max(3., plot_height*item['tokens']/top) if item['tokens'] else 2.
            rect = QRectF(left, baseline-height, max(1, step*.76), height)
            p.setBrush(QBrush(gradient) if item['tokens'] else QBrush(QColor('#e1d8eb')))
            p.drawRoundedRect(rect, 1.3, 1.3)
            self.bar_rects.append(rect)
            self.regions.append((left, left+step, item))

class ShadowSurface(QWidget):
    def paintEvent(self, event):
        # Paint the halo in widget coordinates so that proxy scaling also scales
        # it; native child graphics effects otherwise bypass the proxy transform.
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(18, 18, self.width()-36, self.height()-38)
        for radius in range(16, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 2))
            painter.drawRoundedRect(card.adjusted(-radius, -radius, radius, radius), 23+radius, 23+radius)

class TransparentView(QGraphicsView):
    def drawBackground(self, painter, rect):
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(rect, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

class UsagePanel(QWidget):
    OPEN_MS = 140
    CLOSE_MS = 100
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('Codex Pet · 用量')
        self.content = ShadowSurface()
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.content.setFixedWidth(600)
        self.content.setMinimumHeight(394)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(18, 12, 18, 20)
        self.card = QFrame()
        self.card.setObjectName('popover')
        self.card.setStyleSheet('QFrame#popover{background:#fcfaff;border:1px solid #e8e2ef;border-radius:23px;}')
        outer.addWidget(self.card)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(19, 12, 19, 12)
        layout.setSpacing(10)
        self.left_column = QWidget()
        self.left_column.setFixedWidth(176)
        left_layout = QVBoxLayout(self.left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(7)
        header.addWidget(Mark())
        self.title_button = QPushButton('Codex')
        self.title_button.setFont(font(20, QFont.Weight.Medium))
        self.title_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_button.setStyleSheet('QPushButton{border:0;background:transparent;color:#37313f;padding:0;text-align:left;}QPushButton:hover{color:#8e78a9;}')
        self.title_button.clicked.connect(owner.menu)
        header.addWidget(self.title_button)
        header.addStretch()
        left_layout.addLayout(header)
        self.bucket = QComboBox()
        self.bucket.setFont(font(13))
        self.bucket.setStyleSheet('QComboBox{border:1px solid #e6ddec;border-radius:8px;padding:5px;background:#f7f3fa;color:#80708e;}')
        self.bucket.currentTextChanged.connect(lambda _: self.render())
        self.bucket.hide()
        left_layout.addWidget(self.bucket)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        usage = QWidget()
        uv = QVBoxLayout(usage)
        uv.setContentsMargins(0, 0, 0, 0)
        uv.setSpacing(8)
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.quota = QuotaCard()
        self.quota.setFixedWidth(176)
        left_layout.addWidget(self.quota, 1)
        cards.addWidget(self.left_column)
        self.history_card = right = QFrame()
        right.setObjectName('history')
        right.setStyleSheet('QFrame#history{background:#f6f2f9;border:1px solid #eee8f3;border-radius:18px;}')
        rv = QVBoxLayout(right)
        rv.setContentsMargins(11, 9, 11, 9)
        rv.setSpacing(4)
        self.periods = {}
        filters = QHBoxLayout()
        filters.setSpacing(4)
        filters.addWidget(label('Token', 12, '#8d7d9c'))
        filters.addStretch()
        for name in ('30天', '7天', '今日'):
            b = pill(name, lambda checked=False, n=name: self.set_period(n), name == owner.period)
            self.periods[name] = b
            filters.addWidget(b)
        rv.addLayout(filters)
        self.chart = Chart()
        rv.addWidget(self.chart, 1)
        self.total = label('今日 · —', 18, '#51425e', QFont.Weight.Medium)
        self.total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rv.addWidget(self.total)
        cards.addWidget(right, 1)
        uv.addLayout(cards)
        footer = QFrame()
        footer.setObjectName('footer')
        footer.setStyleSheet('QFrame#footer{background:#f5f1f8;border-radius:13px;}')
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(13, 7, 11, 7)
        fl.addWidget(label('上次消耗 Token', 14, '#8d829a'))
        fl.addStretch()
        self.last_usage = label('—', 18, '#635071', QFont.Weight.Medium)
        fl.addWidget(self.last_usage)
        uv.addWidget(footer)
        self.pages.addWidget(usage)
        activity = QWidget()
        av = QVBoxLayout(activity)
        av.setContentsMargins(0, 0, 0, 0)
        back = pill('‹  返回用量', lambda: self.page('usage'))
        av.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        self.activity = QListWidget()
        self.activity.setMinimumHeight(224)
        self.activity.setStyleSheet('QListWidget{border:0;background:#f7f3fa;border-radius:14px;padding:7px;color:#5f526c;}QListWidget::item{padding:11px;border-radius:9px;}QListWidget::item:selected{background:#e9e0f1;color:#58416b;}')
        self.activity.itemDoubleClicked.connect(self.open_item)
        av.addWidget(self.activity)
        self.pages.addWidget(activity)
        details = QWidget()
        dv = QVBoxLayout(details)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addWidget(pill('‹  返回用量', lambda: self.page('usage')), alignment=Qt.AlignmentFlag.AlignLeft)
        self.sessions = QComboBox()
        self.sessions.setFont(font(13))
        self.sessions.setStyleSheet(self.bucket.styleSheet())
        self.sessions.currentIndexChanged.connect(self.detail_changed)
        dv.addWidget(self.sessions)
        self.detail = label('', 13, '#776584')
        self.detail.setMinimumHeight(190)
        self.detail.setWordWrap(True)
        dv.addWidget(self.detail)
        self.pages.addWidget(details)
        self.error = label('', 12, '#a66b7f')
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        # Render the original layout at precisely 50% on each axis. Proxy input
        # mapping preserves button, combo-box and chart interaction at this size.
        self.scene = QGraphicsScene(self)
        self.proxy = self.scene.addWidget(self.content)
        self.proxy.setScale(.5)
        self.view = TransparentView(self.scene, self)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setStyleSheet('background:transparent;border:none;')
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.TextAntialiasing)
        self.motion = QParallelAnimationGroup(self)
        self.geometry_animation = QPropertyAnimation(self, b'geometry', self.motion)
        self.geometry_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.opacity_animation = QPropertyAnimation(self, b'windowOpacity', self.motion)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.addAnimation(self.geometry_animation)
        self.motion.addAnimation(self.opacity_animation)
        self.motion.finished.connect(self.finish_motion)
        self.transition = None
        self.snapshot = None
        self.target_rect = QRect()
        self.suppressed_until_leave = False
        self.drag_origin = None
        self.sync_size()

    def current_limits(self):
        return self.owner.limits.get(self.bucket.currentText(), next(iter(self.owner.limits.values()), {}))

    def page(self, name):
        self.pages.setCurrentIndex({'usage': 0, 'activity': 1, 'detail': 2}[name])
        self.sync_size()
        if self.isVisible() and not self.transition:
            self.place_near_pet()

    def sync_size(self):
        self.content.adjustSize()
        self.content.layout().activate()
        self.proxy.resize(self.content.size())
        self.scene.setSceneRect(0, 0, self.content.width()*.5, self.content.height()*.5)
        self.view.setGeometry(0, 0, math.ceil(self.content.width()*.5), math.ceil(self.content.height()*.5))
        if not self.transition:
            self.resize(self.view.size())

    def set_period(self, name):
        self.owner.period = name
        self.page('usage')
        self.render()

    def render(self):
        o = self.owner
        limits = self.current_limits()
        limit_id = self.bucket.currentText() or next(iter(o.limits), '')
        self.quota.render(limits, o.request_estimates.get(limit_id))
        for name, b in self.periods.items():
            select(b, name == o.period)
        days = o.filtered_days()
        total = sum(x['tokens'] for x in days) if days is not None else None
        self.total.setText(f'{o.period} · {short(total)}')
        self.chart.series = days or []
        self.chart.loading = days is None
        self.chart.update()
        self.update_last_usage()
        self.sync_size()

    def update_last_usage(self):
        record = last_request_usage(self.owner.snapshots)
        if record is None:
            self.last_usage.setText('—')
            return
        self.last_usage.setText(f"{record['tokens']:,}")

    def update_sessions(self):
        o = self.owner
        self.activity.clear()
        paths = sorted(o.paths, key=lambda p: o.snapshots.get(str(p), {}).get('state') != 'running')
        for path in paths:
            snap = o.snapshots.get(str(path), {})
            title = o.names.get(path.stem[-36:], path.stem[-12:])
            state = STATE_NAMES.get(snap.get('state', 'unknown'), '状态待确认')
            item = QListWidgetItem(f'{title}\n{state}  ·  {snap.get("summary", "")[:45]}')
            item.setFont(font(13))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.activity.addItem(item)
        self.sessions.blockSignals(True)
        self.sessions.clear()
        self.sessions.addItem('自动 · 最近活跃对话', 'auto')
        for path in o.paths:
            self.sessions.addItem(o.names.get(path.stem[-36:], path.stem[-12:]), str(path))
        idx = self.sessions.findData(o.selection)
        self.sessions.setCurrentIndex(max(0, idx))
        self.sessions.blockSignals(False)
        self.detail_changed()
        self.update_last_usage()

    def detail_changed(self):
        o = self.owner
        o.selection = self.sessions.currentData() or 'auto'
        path = str(o.paths[0]) if o.selection == 'auto' and o.paths else o.selection
        snap = o.snapshots.get(path, {})
        info = snap.get('info')
        if not info:
            self.detail.setText('此对话尚未写入 Token 统计。')
            return
        total, last = info.get('total_token_usage') or {}, info.get('last_token_usage') or {}
        def n(obj, key):
            val = obj.get(key)
            return f'{val:,}' if val is not None else '—'
        self.detail.setText(f"累计  {n(total, 'total_tokens')} Tokens\n\n输入  {n(total, 'input_tokens')}  ·  缓存 {n(total, 'cached_input_tokens')}\n输出  {n(total, 'output_tokens')}  ·  推理 {n(total, 'reasoning_output_tokens')}\n\n最近请求  {n(last, 'total_tokens')} Tokens\n\n缓存已计入输入，推理已计入输出。")

    def open_item(self, item):
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        self.owner.open_url('codex://threads/'+path.stem[-36:])

    def place_near_pet(self):
        self.sync_size()
        geo = self.owner.screen().availableGeometry()
        capsule = self.owner.toolbar.capsule_global_rect()
        target = clear_panel_rect(geo, self.owner.protected_rects(), self.view.size(), capsule)
        if target is None:
            if self.isVisible():
                self.retract()
            return False
        old_target = self.target_rect
        self.target_rect = target
        if not self.transition:
            self.setGeometry(self.target_rect)
        elif self.transition == 'opening' and old_target != target:
            self.motion.stop()
            current = self.map_transition_rect(self.geometry(), old_target, target)
            self.setGeometry(current)
            self.geometry_animation.setStartValue(current)
            self.geometry_animation.setEndValue(self.target_rect)
            self.set_motion_duration(current, self.target_rect, self.OPEN_MS)
            self.motion.start()
        elif self.transition == 'closing' and old_target != target:
            self.hide()
        return True

    @staticmethod
    def map_transition_rect(current, old_target, target):
        if old_target.isEmpty():
            return QRect(target)
        sx, sy = target.width()/old_target.width(), target.height()/old_target.height()
        w = max(1, min(target.width(), round(current.width()*sx)))
        h = max(1, min(target.height(), round(current.height()*sy)))
        x = target.x()+round((current.x()-old_target.x())*sx)
        y = target.y()+round((current.y()-old_target.y())*sy)
        return QRect(max(target.left(), min(x, target.right()-w+1)), max(target.top(), min(y, target.bottom()-h+1)), w, h)

    def card_global_rect(self, target=False):
        geometry = self.target_rect if target else self.geometry()
        rect = QRectF(self.card.geometry())
        sx = geometry.width()/max(1, self.view.width())*self.proxy.scale()
        sy = geometry.height()/max(1, self.view.height())*self.proxy.scale()
        return QRectF(geometry.x()+rect.x()*sx, geometry.y()+rect.y()*sy, rect.width()*sx, rect.height()*sy)

    def contains_pointer(self, point):
        if not self.isVisible():
            return False
        rect = self.card_global_rect()
        radius = 23*self.proxy.scale()*min(self.width()/max(1, self.view.width()), self.height()/max(1, self.view.height()))
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        return shape.contains(QPointF(point))

    def has_active_popup(self):
        if self.owner.panel_menu_open:
            return True
        popup = QApplication.activePopupWidget()
        return popup is not None and (self.isAncestorOf(popup) or self.content.isAncestorOf(popup))

    def in_transfer_gap(self, point):
        a, b = QRectF(self.owner.toolbar.capsule_global_rect()), self.card_global_rect(target=True)
        if b.left() >= a.right():
            y = max(b.top()+12, min(a.center().y(), b.bottom()-12))
            points = (a.topRight(), a.bottomRight(), QPointF(b.left(), y+12), QPointF(b.left(), y-12))
        elif b.right() <= a.left():
            y = max(b.top()+12, min(a.center().y(), b.bottom()-12))
            points = (a.topLeft(), QPointF(b.right(), y-12), QPointF(b.right(), y+12), a.bottomLeft())
        elif b.top() >= a.bottom():
            x = max(b.left()+12, min(a.center().x(), b.right()-12))
            points = (QPointF(a.center().x()-12, a.bottom()), QPointF(a.center().x()+12, a.bottom()), QPointF(x+12, b.top()), QPointF(x-12, b.top()))
        else:
            x = max(b.left()+12, min(a.center().x(), b.right()-12))
            points = (QPointF(a.center().x()-12, a.top()), QPointF(x-12, b.bottom()), QPointF(x+12, b.bottom()), QPointF(a.center().x()+12, a.top()))
        path = QPainterPath(points[0])
        for vertex in points[1:]:
            path.lineTo(vertex)
        path.closeSubpath()
        return path.contains(QPointF(point))

    def set_motion_duration(self, start, end, base):
        seed = self.seed_rect()
        fraction = max(abs(start.width()-end.width())/max(1, self.target_rect.width()-seed.width()), abs(start.height()-end.height())/max(1, self.target_rect.height()-seed.height()))
        duration = max(35, round(base*min(1., fraction)))
        self.geometry_animation.setDuration(duration)
        self.opacity_animation.setDuration(duration)

    def seed_rect(self):
        # Both ends of the animation lie inside the already safe target. Every
        # interpolated frame is therefore safe too, without cutting any holes.
        target = self.target_rect
        anchor = self.owner.toolbar.capsule_global_rect().center()
        w, h = min(142, target.width()), min(40, target.height())
        x = max(target.left(), min(anchor.x()-w//2, target.right()-w+1))
        y = max(target.top(), min(anchor.y()-h//2, target.bottom()-h+1))
        return QRect(x, y, w, h)

    def reveal(self, page='usage'):
        if self.suppressed_until_leave:
            return
        if self.isVisible() and self.transition != 'closing':
            return
        reopening = self.isVisible() and self.transition == 'closing'
        current, old_target = QRect(self.geometry()), QRect(self.target_rect)
        snapshot = self.snapshot if reopening else None
        self.motion.stop()
        self.transition = None
        self.page(page)
        if not self.place_near_pet():
            return
        begin = self.map_transition_rect(current, old_target, self.target_rect) if reopening else self.seed_rect()
        self.setGeometry(begin)
        if snapshot is None:
            self.view.show()
            snapshot = self.view.grab()
        self.snapshot = snapshot
        self.view.hide()
        self.transition = 'opening'
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self.set_motion_duration(begin, self.target_rect, self.OPEN_MS)
        self.geometry_animation.setStartValue(self.geometry())
        self.geometry_animation.setEndValue(self.target_rect)
        self.opacity_animation.setStartValue(1.0)
        self.opacity_animation.setEndValue(1.0)
        self.motion.start()

    def retract(self):
        if not self.isVisible() or self.transition == 'closing':
            return
        self.motion.stop()
        if self.view.isVisible():
            self.snapshot = self.view.grab()
        self.view.hide()
        self.transition = 'closing'
        self.set_motion_duration(self.geometry(), self.seed_rect(), self.CLOSE_MS)
        self.geometry_animation.setStartValue(self.geometry())
        self.geometry_animation.setEndValue(self.seed_rect())
        self.opacity_animation.setStartValue(1.0)
        self.opacity_animation.setEndValue(1.0)
        self.motion.start()

    def finish_motion(self):
        closing = self.transition == 'closing'
        self.transition = None
        self.snapshot = None
        if closing:
            self.hide()
        else:
            self.setGeometry(self.target_rect)
            self.view.show()
        self.setWindowOpacity(1.0)

    def dismiss(self):
        self.suppressed_until_leave = True
        self.retract()

    def paintEvent(self, event):
        if self.transition and self.snapshot:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), self.snapshot)

    def hide(self):
        self.motion.stop()
        self.transition = None
        self.snapshot = None
        super().hide()
        self.view.show()
        self.setWindowOpacity(1.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 78:
            self.drag_origin = event.globalPosition().toPoint()-self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint()-self.drag_origin)

    def mouseReleaseEvent(self, event):
        self.drag_origin = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

class SpeechBar(QWidget):
    """Read-only conversation title and latest assistant progress, like the pet tray."""
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setWindowTitle('Codex Pet · 对话进度')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(336, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title, self.summary, self.path = '', '', None
        self.was_running = False
        self.expires_at = 0

    def sync_activity(self):
        o = self.owner
        active = next((p for p in o.paths if o.snapshots.get(str(p), {}).get('state') == 'running'), None)
        if active:
            self.path = active
            snap = o.snapshots[str(active)]
            self.title = o.names.get(active.stem[-36:], 'Codex 对话')
            self.summary = snap.get('summary') or '正在处理这条对话…'
            if snap.get('summary_time') and snap.get('active_time'):
                try:
                    previous = datetime.fromisoformat(snap['summary_time'].replace('Z', '+00:00'))
                    started = datetime.fromisoformat(snap['active_time'].replace('Z', '+00:00'))
                    if previous < started:
                        self.summary = '正在处理这条对话…'
                except ValueError:
                    pass
            self.was_running, self.expires_at = True, 0
            self.show()
        elif self.was_running:
            snap = o.snapshots.get(str(self.path), {})
            if snap.get('state') in ('ready', 'stopped'):
                self.summary = snap.get('summary') or STATE_NAMES[snap['state']]
                self.expires_at = time.monotonic()+5
            else:
                self.hide()
            self.was_running = False
        self.owner.layout_stack()
        self.update()

    def position_near_pet(self):
        area = self.owner.screen().availableGeometry()
        x = self.owner.x()+self.owner.width()//2-self.width()//2
        y = self.owner.y()+round(self.owner.pet_size*208/192)+4
        self.move(max(area.left(), min(x, area.right()-self.width()+1)), max(area.top(), y))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for radius in range(6, 0, -1):
            p.setBrush(QColor(0, 0, 0, 2))
            p.drawRoundedRect(QRectF(6-radius, 6-radius, self.width()-12+2*radius, self.height()-14+2*radius), 20+radius, 20+radius)
        p.setPen(QPen(QColor(230, 224, 237, 210), .8))
        p.setBrush(QColor(255, 253, 255, 246))
        p.drawRoundedRect(QRectF(5, 4, self.width()-10, self.height()-12), 23, 23)
        available = self.width()-34
        p.setFont(font(9, QFont.Weight.DemiBold))
        p.setPen(QColor('#53495d'))
        p.drawText(QRectF(17, 11, available, 18), Qt.AlignmentFlag.AlignVCenter, QFontMetrics(p.font()).elidedText(self.title, Qt.TextElideMode.ElideRight, available))
        p.setFont(font(9))
        p.setPen(QColor('#9b92a3'))
        p.drawText(QRectF(17, 29, available, 19), Qt.AlignmentFlag.AlignVCenter, QFontMetrics(p.font()).elidedText(self.summary, Qt.TextElideMode.ElideRight, available))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.path:
            self.owner.open_url('codex://threads/'+self.path.stem[-36:])
        event.accept()

class DesktopPet(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('Codex Pet · 桌宠')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.tooltip_filter = NoToolTips(self)
        QApplication.instance().installEventFilter(self.tooltip_filter)
        self.settings = read_json(BASE/'settings.json', {})
        self.selection = self.settings.get('session', 'auto')
        self.pet_size = self.settings.get('pet_size', 112)
        self.animate = self.settings.get('animate', True)
        self.period = '今日'
        self.account, self.limits, self.usage = {}, {}, None
        self.request_estimates = {}
        self.rolling_days = None
        self.today_usage = None
        self.usage_day = local_now().date().isoformat()
        self.rolling = RollingUsage()
        self.paths, self.names, self.snapshots = [], {}, {}
        self.busy, self.scanning = False, False
        self.last_update = None
        self.messages = queue.Queue()
        self.frames, self.frame = {}, 0
        self.mood, self.reaction_end = 0, 0
        self.drag_origin, self.dragged = None, False
        self.hover_until = 0
        self.capsule_hover_since = None
        self.panel_leave_since = None
        self.panel_menu_open = False
        self.panel = UsagePanel(self)
        self.toolbar = Toolbar(self)
        self.toolbar.show()
        self.speech = SpeechBar(self)
        self.stack_motion = QPropertyAnimation(self.toolbar, b'pos', self)
        self.stack_motion.setDuration(190)
        self.stack_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.stack_motion.finished.connect(self.finish_stack_layout)
        self.stack_target_height = 0
        self.load_pet()
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(max(screen.left(), min(self.settings.get('x', screen.right()-300), screen.right()-self.width()+1)), max(screen.top(), min(self.settings.get('y', screen.bottom()-260), screen.bottom()-self.height()+1)))
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(130)
        self.hover_timer = QTimer(self)
        self.hover_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.hover_timer.timeout.connect(lambda: self.handle_pointer(QCursor.pos(), time.monotonic()))
        self.hover_timer.start(16)
        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.poll)
        self.queue_timer.start(100)
        self.quota_timer = QTimer(self)
        self.quota_timer.timeout.connect(self.refresh)
        self.quota_timer.start(60000)
        self.sessions_timer = QTimer(self)
        self.sessions_timer.timeout.connect(self.scan)
        self.sessions_timer.start(5000)
        self.refresh()
        self.scan()

    def load_pet(self, pet_id=None):
        pets = list((HOME/'pets').glob('*/pet.json'))
        wanted = pet_id or self.settings.get('pet', 'zili')
        conf = next((p for p in pets if p.parent.name == wanted), pets[0] if pets else None)
        self.frames = {}
        if conf:
            meta = read_json(conf, {})
            try:
                sheet = Image.open(conf.parent/meta.get('spritesheetPath', 'spritesheet.webp')).convert('RGBA')
                if sheet.width == 1536 and sheet.height in (1872, 2288):
                    for row, count in enumerate(FRAME_COUNTS[:sheet.height//208]):
                        frames = []
                        for col in range(count):
                            image = sheet.crop((col*192, row*208, (col+1)*192, (row+1)*208))
                            q = QImage(image.tobytes(), 192, 208, QImage.Format.Format_RGBA8888).copy()
                            ratio = self.devicePixelRatioF()
                            pixmap = QPixmap.fromImage(q).scaled(round(self.pet_size*ratio), round(self.pet_size*208/192*ratio), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                            pixmap.setDevicePixelRatio(ratio)
                            frames.append(pixmap)
                        self.frames[row] = frames
                    self.settings['pet'] = conf.parent.name
            except (OSError, ValueError):
                pass
        self.setFixedWidth(284)
        self.layout_stack(animated=False)
        if self.panel.isVisible():
            self.panel.place_near_pet()
        self.update()

    def layout_stack(self, animated=True):
        body_height = round(self.pet_size*208/192)
        summary_gap = self.speech.height()+8 if self.speech.isVisible() else 0
        target = QPoint((self.width()-self.toolbar.width())//2, body_height+5+summary_gap)
        self.stack_target_height = target.y()+self.toolbar.height()+5
        self.setFixedHeight(max(self.height(), self.stack_target_height))
        screen = self.screen().availableGeometry()
        if self.y()+self.stack_target_height > screen.bottom()+1:
            self.move(self.x(), max(screen.top(), screen.bottom()-self.stack_target_height+1))
        self.speech.position_near_pet()
        if animated and self.toolbar.pos() != target:
            if self.stack_motion.endValue() != target or self.stack_motion.state() != QPropertyAnimation.State.Running:
                self.stack_motion.stop()
                self.stack_motion.setStartValue(self.toolbar.pos())
                self.stack_motion.setEndValue(target)
                self.stack_motion.start()
        else:
            self.stack_motion.stop()
            self.toolbar.move(target)
            self.finish_stack_layout()
        if self.panel.isVisible():
            self.panel.place_near_pet()

    def finish_stack_layout(self):
        self.setFixedHeight(self.stack_target_height)
        self.speech.position_near_pet()
        if self.panel.isVisible():
            self.panel.place_near_pet()

    def sprite_global_rect(self):
        return QRect(self.x()+(self.width()-self.pet_size)//2-2, self.y()-2, self.pet_size+4, round(self.pet_size*208/192)+4)

    def protected_rects(self):
        # Protect painted surfaces rather than the pet window's empty margins,
        # so the panel can sit close to the action row without covering it.
        rects = [self.sprite_global_rect().adjusted(-3, -3, 3, 3)]
        if self.toolbar.isVisible():
            rects.append(QRect(self.toolbar.mapToGlobal(QPoint()), self.toolbar.size()))
        if self.speech.isVisible():
            rects.append(self.speech.frameGeometry().adjusted(-3, -3, 3, 3))
        return rects

    def visual_obstacles(self):
        rects = [self.sprite_global_rect()]
        if self.speech.isVisible():
            rects.append(self.speech.frameGeometry())
        return rects

    def remaining(self, window='primary'):
        limits = self.panel.current_limits()
        used = (limits.get(window) or {}).get('usedPercent')
        return max(0, min(100, 100-used)) if used is not None else None

    def running_count(self):
        return sum(s.get('state') == 'running' for s in self.snapshots.values())

    def tick(self):
        day = local_now().date().isoformat()
        if day != self.usage_day:
            self.usage_day = day
            self.today_usage = None
            self.panel.render()
            self.refresh()
        if self.animate:
            self.frame += 1
        if self.speech.expires_at and time.monotonic() >= self.speech.expires_at:
            self.speech.hide()
            self.speech.expires_at = 0
            self.layout_stack()
        self.update()

    def handle_pointer(self, mouse, now):
        if self.frameGeometry().contains(mouse):
            self.hover_until = now+.65
        self.toolbar.setVisible(True)
        self.toolbar.update_proximity(mouse)
        capsule = self.toolbar.capsule_contains(mouse)
        inside_panel = self.panel.contains_pointer(mouse)
        popup = self.panel.has_active_popup()
        if capsule:
            if self.capsule_hover_since is None:
                self.capsule_hover_since = now
            if now-self.capsule_hover_since >= .08:
                self.panel.reveal()
        else:
            self.capsule_hover_since = None
            if not inside_panel and not popup:
                self.panel.suppressed_until_leave = False
        if self.panel.isVisible():
            if capsule or inside_panel or popup:
                self.panel_leave_since = None
                if self.panel.transition == 'closing' and not self.panel.suppressed_until_leave:
                    self.panel.reveal()
            else:
                if self.panel_leave_since is None:
                    self.panel_leave_since = now
                other_ring = self.toolbar.item_at(self.toolbar.mapFromGlobal(mouse)) == self.toolbar.QUOTA_INDEX
                grace = .18 if not other_ring and self.panel.in_transfer_gap(mouse) else .06
                if now-self.panel_leave_since >= grace:
                    self.panel.retract()
        else:
            self.panel_leave_since = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.frames:
            p.setPen(QColor(PURPLE))
            p.drawText(QRectF(0, 0, self.width(), 90), Qt.AlignmentFlag.AlignCenter, '未找到宠物素材')
            return
        row = self.mood if time.time() < self.reaction_end else 7 if self.running_count() else 0
        index = self.frame if self.animate else 0
        if row == 0 and 9 in self.frames and self.animate:
            mouse = self.mapFromGlobal(QCursor.pos())
            dx, dy = mouse.x()-self.width()/2, mouse.y()-self.pet_size*.54
            if 40 < math.hypot(dx, dy) < 420:
                index = round(math.degrees(math.atan2(dx, -dy))/22.5) % 16
                row, index = 9+index//8, index % 8
        frames = self.frames.get(row, self.frames[0])
        image = frames[index % len(frames)]
        p.drawPixmap(QPointF((self.width()-image.deviceIndependentSize().width())/2, 0), image)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = event.globalPosition().toPoint()-self.pos()
            self.dragged = False

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            target = event.globalPosition().toPoint()-self.drag_origin
            if (target-self.pos()).manhattanLength() > 2:
                self.dragged = True
                self.react(1 if target.x() >= self.x() else 2, .3)
                self.move(target)
                self.speech.position_near_pet()
                if self.panel.isVisible():
                    self.panel.place_near_pet()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.dragged:
            self.react(3)
        self.drag_origin = None

    def react(self, mood, seconds=1.5):
        self.mood, self.frame, self.reaction_end = mood, 0, time.time()+seconds
        self.update()

    def change_size(self, size):
        self.pet_size = size
        self.load_pet()

    def toggle_animation(self):
        self.animate = not self.animate

    def menu(self):
        menu = QMenu()
        menu.setStyleSheet('QMenu{background:#fcfaff;color:#61516f;border:1px solid #e7dfee;border-radius:10px;padding:6px;}QMenu::item{padding:7px 20px;border-radius:6px;}QMenu::item:selected{background:#eee6f5;}')
        sizes = menu.addMenu('宠物大小')
        for size in (80, 96, 112, 128, 160):
            action = sizes.addAction(f'{size}px')
            action.triggered.connect(lambda checked=False, s=size: self.change_size(s))
        pets = menu.addMenu('选择宠物')
        for path in (HOME/'pets').glob('*/pet.json'):
            meta = read_json(path, {})
            action = pets.addAction(meta.get('displayName', path.parent.name))
            action.triggered.connect(lambda checked=False, p=path: self.load_pet(p.parent.name))
        menu.addSeparator()
        menu.addAction('退出桌宠', self.exit)
        self.panel_menu_open = True
        try:
            menu.exec(QCursor.pos())
        finally:
            self.panel_menu_open = False

    def open_url(self, url):
        try:
            os.startfile(url)
        except OSError:
            QToolTip.showText(QCursor.pos(), '请在 Codex 主应用中打开对话。')

    def filtered_days(self):
        daily = self.usage.get('dailyUsageBuckets') if self.usage is not None else None
        return fixed_usage_series(self.period, daily, self.today_usage)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        def run():
            estimate_limits = dict(self.limits)
            try:
                account = fetch_account()
                self.messages.put(('account', account))
                estimate_limits = account[0].get('rateLimitsByLimitId') or ({'codex': account[0]['rateLimits']} if account[0].get('rateLimits') else {})
            except Exception:
                self.messages.put(('error', '刷新失败，保留上次数据。请确认 Codex 已登录。'))
            try:
                days = self.rolling.read()
                estimates = {key: self.rolling.estimate_requests(value, key) for key, value in estimate_limits.items()}
                today = self.rolling.today_usage()
                self.messages.put(('rolling', days))
                self.messages.put(('estimates', estimates))
                self.messages.put(('today', today))
            except Exception:
                self.messages.put(('rolling', None))
        threading.Thread(target=run, daemon=True).start()

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        def run():
            try:
                paths, snapshots = scan_sessions(), {}
                for p in paths:
                    st = p.stat()
                    previous = self.snapshots.get(str(p))
                    if previous and previous.get('stamp') == (st.st_mtime_ns, st.st_size):
                        snap = dict(previous)
                        if snap.get('state') == 'running' and time.time()-snap.get('last_record_time', st.st_mtime) > 900:
                            snap['state'] = 'unknown'
                    else:
                        snap = session_snapshot(p)
                    snap['stamp'] = (st.st_mtime_ns, st.st_size)
                    snapshots[str(p)] = snap
                paths.sort(key=lambda p: snapshots[str(p)].get('last_record_time', 0), reverse=True)
                self.messages.put(('sessions', (paths, session_names(), snapshots)))
            except Exception:
                self.messages.put(('scan_error', None))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == 'account':
                    self.account, self.usage = data
                    self.limits = self.account.get('rateLimitsByLimitId') or {}
                    if not self.limits and self.account.get('rateLimits'):
                        self.limits = {'codex': self.account['rateLimits']}
                    self.last_update = datetime.now().strftime('%H:%M:%S')
                    current = self.panel.bucket.currentText()
                    self.panel.bucket.blockSignals(True)
                    self.panel.bucket.clear()
                    self.panel.bucket.addItems(list(self.limits))
                    if current in self.limits:
                        self.panel.bucket.setCurrentText(current)
                    self.panel.bucket.blockSignals(False)
                    self.panel.bucket.setVisible(len(self.limits) > 1)
                    self.panel.error.hide()
                    self.panel.render()
                elif kind == 'error':
                    self.panel.error.setText(data+(' 上次更新 '+self.last_update if self.last_update else ''))
                    self.panel.error.show()
                elif kind == 'rolling':
                    self.busy = False
                    self.rolling_days = data
                    self.panel.render()
                elif kind == 'estimates':
                    self.request_estimates = data
                    self.panel.render()
                elif kind == 'today':
                    self.today_usage = data
                    self.panel.render()
                elif kind == 'sessions':
                    self.scanning = False
                    self.paths, self.names, self.snapshots = data
                    self.panel.update_sessions()
                    self.speech.sync_activity()
                    if self.panel.isVisible():
                        self.panel.place_near_pet()
                elif kind == 'scan_error':
                    self.scanning = False
        except queue.Empty:
            pass
        self.toolbar.update()

    def copy_stats(self):
        limits = self.panel.current_limits()
        lines = ['Codex 用量 · '+datetime.now().strftime('%Y-%m-%d %H:%M')]
        for key, name in (('primary', '5h剩余'), ('secondary', '周剩余')):
            item = limits.get(key) or {}
            used = item.get('usedPercent')
            lines.append(name+' '+(f'{max(0, min(100, 100-used)):g}%' if used is not None else '未知'))
        lines.append('上次消耗 Token '+self.panel.last_usage.text())
        lines.append(self.panel.detail.text())
        QApplication.clipboard().setText('\n'.join(lines))
        QToolTip.showText(QCursor.pos(), '用量摘要已复制')

    def exit(self):
        self.settings.update(x=self.x(), y=self.y(), session=self.selection, pet_size=self.pet_size, animate=self.animate)
        try:
            (BASE/'settings.json').write_text(json.dumps(self.settings, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass
        self.panel.hide()
        self.speech.hide()
        QApplication.quit()

def main():
    configure_display_scale()
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\CodexPetUsagePanel')
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(font(10))
    app.setStyleSheet('QToolTip{background:#fcfaff;color:#6f5c7e;border:1px solid #e7ddee;border-radius:6px;padding:6px;}')
    pet = DesktopPet()
    pet.show()
    app.exec()
    ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == '__main__':
    main()
