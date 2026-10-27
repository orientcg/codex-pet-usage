"""Exercise dragging, animation continuity and sprite bounds with no account reads."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pet_desktop as m
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

m.fetch_account = lambda: ({}, None)
m.fetch_vps_traffic = lambda: None
m.scan_sessions = lambda: []
m.session_names = lambda: {}
m.RollingUsage.read = lambda self: []
m.configure_display_scale()
app = QApplication([])
pet = m.DesktopPet()
pet.show()
for timer in (pet.hover_timer, pet.sessions_timer, pet.quota_timer, pet.tick_timer, pet.vps_timer):
    timer.stop()
QTest.qWait(200)
pet.queue_timer.stop()
assert pet.frames, 'Install a supported pet before running this native rendering check'
assert pet.tick_timer.interval() == 100


def mouse(kind, global_pos, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton):
    local = pet.mapFromGlobal(global_pos)
    event = QMouseEvent(kind, QPointF(local), QPointF(global_pos), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pet, event)


origin = pet.pos()
press = pet.mapToGlobal(QPoint(pet.width()//2-25, 35))
mouse(QEvent.Type.MouseButtonPress, press, Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseMove, press+QPoint(1, 1))
assert not pet.carrying and pet.pos() == origin
mouse(QEvent.Type.MouseButtonRelease, press, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
assert pet.mood == 3 and not pet.carrying  # A click still waves.

mouse(QEvent.Type.MouseButtonPress, press, Qt.MouseButton.LeftButton)
pointer = press+QPoint(25, -15)
mouse(QEvent.Type.MouseMove, pointer)
assert pet.carrying and pet.dragged
assert pet.carry_hand == -1 and pet.animation_frame() == (m.CARRY_ROW, 1)
assert (pet.mapToGlobal(pet.carry_anchor().toPoint())-pointer).manhattanLength() == 0
for step, dx in enumerate((12, -30, 18, 0, -15, 10, -5, 8), 1):
    pet.tick()
    frame = pet.frame
    pointer += QPoint(dx, 2)
    mouse(QEvent.Type.MouseMove, pointer)
    assert pet.frame == frame, 'Movement must never restart the animation clock'
    assert pet.animation_frame() == (m.CARRY_ROW, 1), 'The raised hand must stay in the same pose'
    assert pet.dragged and pet.carrying
    assert (pet.mapToGlobal(pet.carry_anchor().toPoint())-pointer).manhattanLength() == 0
    pet.grab()

QTest.qWait(170)
still = pet.grab().toImage()
for _ in range(3):
    pet.tick()
    assert pet.grab().toImage() == still, 'Holding still must not wiggle, rotate or stretch the body'

pet.panel.reveal()
pet.handle_pointer(pet.toolbar.capsule_global_rect().center(), time.monotonic())
assert pet.panel.transition == 'closing' or not pet.panel.isVisible()
for size in (80, 96, 112, 128, 160):
    pet.change_size(size)
    for hand in (-1, 1):
        pet.carry_hand = hand
        sprite = pet.sprite_global_rect().translated(-pet.pos())
        assert pet.rect().contains(sprite), (size, hand, sprite, pet.rect())
        assert sprite.bottom() < pet.toolbar.y(), (size, hand, sprite, pet.toolbar.y())
pet.animate = False
assert pet.animation_frame() == (m.CARRY_ROW, 2)
mouse(QEvent.Type.MouseButtonRelease, pointer, Qt.MouseButton.RightButton)
assert pet.carrying  # An unrelated button cannot terminate the left-button drag.
mouse(QEvent.Type.MouseButtonRelease, pointer, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
assert not pet.carrying and pet.drag_origin is None
assert pet.mood == 4
assert pet.animation_frame()[0] == 4
pet.reaction_end = 0
pet.snapshots = {'example': {'state': 'running'}}
assert pet.animation_frame()[0] == 7
pet.animate = True
press = pet.mapToGlobal(QPoint(pet.width()//2+25, 35))
mouse(QEvent.Type.MouseButtonPress, press, Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseMove, press+QPoint(-20, -10))
assert pet.carry_hand == 1 and pet.animation_frame() == (m.CARRY_ROW, 2)
mouse(QEvent.Type.MouseButtonRelease, press, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)

# A transition begins at the outgoing image, remains opaque, and supports interruption.
red, blue, green = (QPixmap(16, 16) for _ in range(3))
for image, color in ((red, '#dc1414'), (blue, '#1414dc'), (green, '#14dc14')):
    image.setDevicePixelRatio(2)
    image.fill(QColor(color))
blend = m.SpriteTransition()
assert blend.image(red, 'idle', 1.).toImage() == red.toImage()
assert blend.image(blue, 'working', 2.).toImage() == red.toImage()
middle = blend.image(blue, 'working', 2.07)
assert middle.devicePixelRatio() == 2 and middle.size() == blue.size()
color = middle.toImage().pixelColor(8, 8)
assert 20 < color.red() < 220 and 20 < color.blue() < 220 and color.alpha() >= 254
assert blend.image(green, 'held', 2.07).toImage() == middle.toImage(), 'Interruption must continue from the current pose'
assert blend.image(green, 'held', 2.22).toImage() == green.toImage()
assert blend.source is None
assert blend.image(blue, 'working', 2.3, animated=False).toImage() == blue.toImage()
assert blend.source is None
blend.reset()
assert blend.image(red, 'idle', 3.).toImage() == red.toImage()
QTest.qWait(220)
pet.panel.hide()
pet.speech.hide()
pet.hide()
app.quit()
print('PASS: 10 fps; nearest hand follows pointer; stable body while held; both hands and all sizes stay unclipped; panel stays closed; release restores normal states; opaque smooth transitions continue correctly on interruption')
