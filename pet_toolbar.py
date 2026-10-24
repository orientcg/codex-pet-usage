"""Three independent circular pet actions that expand under the pointer."""
from PySide6.QtCore import Qt, QRectF, QPoint, QPointF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QApplication
from codex_data import BASE


def text_font(size, weight=QFont.Weight.Normal):
    return QFont('Microsoft YaHei UI', size, weight)


class Toolbar(QWidget):
    DIAMETER = 38.0
    GAP = 8.0
    EXPANDED = (90.0, 90.0, 90.0)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setFixedSize(268, 46)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon = QSvgRenderer(str(BASE/'codex-app.svg'), self)
        self.expansions = [0.0, 0.0, 0.0]
        self.starts = self.expansions.copy()
        self.targets = self.expansions.copy()
        self.origin = (self.width()-3*self.DIAMETER-2*self.GAP)/2
        self.origin_start = self.origin_target = self.origin
        self.active_index = None
        self.motion = QVariantAnimation(self)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.motion.setDuration(220)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.interpolate)

    def interpolate(self, progress):
        self.expansions = [a+(b-a)*float(progress) for a, b in zip(self.starts, self.targets)]
        self.origin = self.origin_start+(self.origin_target-self.origin_start)*float(progress)
        self.update()

    def set_active(self, index):
        targets = [float(i == index) for i in range(3)]
        self.active_index = index
        if targets == self.targets:
            return
        self.motion.stop()
        anchor = self.item_rects()[index].center().x() if index is not None else None
        self.starts = self.expansions.copy()
        self.targets = targets
        widths = [self.EXPANDED[i] if i == index else self.DIAMETER for i in range(3)]
        total = sum(widths)+2*self.GAP
        desired = (self.width()-total)/2 if index is None else anchor-sum(widths[:index])-index*self.GAP-widths[index]/2
        self.origin_start = self.origin
        self.origin_target = max(4., min(desired, self.width()-total-4.))
        self.motion.start()

    def item_rects(self):
        widths = [self.DIAMETER+(end-self.DIAMETER)*t
                  for end, t in zip(self.EXPANDED, self.expansions)]
        left = self.origin
        result = []
        for width in widths:
            result.append(QRectF(left, 4, width, self.DIAMETER))
            left += width+self.GAP
        return result

    def item_at(self, point, keep_active=False):
        point = QPointF(point)
        rects = self.item_rects()
        order = list(range(3))
        if keep_active and self.active_index is not None:
            order.remove(self.active_index)
            order.insert(0, self.active_index)
        for i in order:
            rect = rects[i].adjusted(-2, -3, 2, 3)
            shape = QPainterPath()
            shape.addRoundedRect(rect, rect.height()/2, rect.height()/2)
            if shape.contains(point):
                return i
        return None

    def update_proximity(self, global_point):
        local = self.mapFromGlobal(global_point)
        index = self.item_at(local, keep_active=True)
        panel = self.owner.panel
        if (index is None and panel.isVisible() and panel.transition != 'closing'
                and (panel.contains_pointer(global_point) or panel.has_active_popup())):
            index = 1
        self.set_active(index)

    def capsule_local_rect(self):
        return self.item_rects()[1]

    def capsule_global_rect(self):
        return self.capsule_local_rect().toAlignedRect().translated(self.mapToGlobal(QPoint(0, 0)))

    def capsule_contains(self, global_point):
        shape = QPainterPath()
        rect = self.capsule_local_rect()
        shape.addRoundedRect(rect, rect.height()/2, rect.height()/2)
        return self.isVisible() and self.active_index == 1 and shape.contains(QPointF(self.mapFromGlobal(global_point)))

    @staticmethod
    def draw_compose(p, centre):
        x, y = centre.x(), centre.y()
        p.setPen(QPen(QColor('#56505f'), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        outline = QPainterPath()
        outline.moveTo(x+1, y-7)
        outline.lineTo(x-4, y-7)
        outline.quadTo(x-7, y-7, x-7, y-4)
        outline.lineTo(x-7, y+4)
        outline.quadTo(x-7, y+7, x-4, y+7)
        outline.lineTo(x+4, y+7)
        outline.quadTo(x+7, y+7, x+7, y+4)
        outline.lineTo(x+7, y+1)
        p.drawPath(outline)
        nib = QPainterPath()
        nib.moveTo(x-1, y+2)
        nib.lineTo(x, y-2)
        nib.lineTo(x+7, y-9)
        nib.lineTo(x+10, y-6)
        nib.lineTo(x+3, y+1)
        nib.closeSubpath()
        p.drawPath(nib)

    @staticmethod
    def draw_voice(p, centre):
        p.setPen(QPen(QColor('#56505f'), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for dx, height in ((-6, 4), (-3, 10), (0, 16), (3, 8), (6, 5)):
            p.drawLine(QPointF(centre.x()+dx, centre.y()-height/2), QPointF(centre.x()+dx, centre.y()+height/2))

    def draw_quota(self, p, centre, value):
        circle = QRectF(centre.x()-14, centre.y()-14, 28, 28)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor('#e6deec'), 2.5))
        p.drawEllipse(circle)
        if value is not None:
            p.setPen(QPen(QColor('#927bad'), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(circle, 90*16, -round(value*3.6*16))
        p.setPen(QColor('#63516f'))
        p.setFont(text_font(7, QFont.Weight.Medium))
        p.drawText(circle.adjusted(-2, 0, 2, 0), Qt.AlignmentFlag.AlignCenter, f'{value:.0f}%' if value is not None else '—')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for i, rect in enumerate(self.item_rects()):
            p.setPen(Qt.PenStyle.NoPen)
            for spread, alpha in ((2, 5), (1, 9)):
                p.setBrush(QColor(49, 35, 65, alpha))
                p.drawRoundedRect(rect.adjusted(-spread, -spread+1, spread, spread+1), 20+spread, 20+spread)
            p.setPen(QPen(QColor('#e7e1ed'), 1))
            p.setBrush(QColor('#fcfaff'))
            p.drawRoundedRect(rect, 19, 19)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(rect, 19, 19)
            p.setClipPath(clip)
            t = self.expansions[i]
            centre = rect.center()
            if i == 0:
                self.draw_compose(p, centre-QPointF(22*t, 0))
                p.setOpacity(t)
                p.setPen(QPen(QColor('#e5e1e9'), 1))
                p.drawLine(QPointF(centre.x(), 13), QPointF(centre.x(), 33))
                self.draw_voice(p, centre+QPointF(22, 0))
            elif i == 1:
                icon_centre = centre-QPointF(26*t, 0)
                self.icon.render(p, QRectF(icon_centre.x()-10, centre.y()-10, 20, 20))
                p.setOpacity(t)
                p.setPen(QColor('#61546d'))
                p.setFont(text_font(8))
                text_rect = QRectF(icon_centre.x()+17, rect.top(), rect.right()-icon_centre.x()-27, rect.height())
                text = f'运行 {self.owner.running_count()}'
                if text_rect.width() > 0:
                    p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                               QFontMetrics(p.font()).elidedText(text, Qt.TextElideMode.ElideMiddle, int(text_rect.width())))
            else:
                primary = self.owner.remaining('primary')
                secondary = self.owner.remaining('secondary')
                first = centre-QPointF(23*t, 0)
                self.draw_quota(p, first, primary)
                p.setOpacity(t)
                self.draw_quota(p, first+QPointF(46, 0), secondary)
            p.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.item_at(event.position()) == 0:
            if self.expansions[0] > .7 and event.position().x() > self.item_rects()[0].center().x():
                self.owner.voice_chat()
            else:
                self.owner.new_chat()
        event.accept()
