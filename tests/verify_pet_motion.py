"""Exercise dragging, animation continuity and sprite bounds with no account reads."""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pet_desktop as m
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

m.fetch_account = lambda: ({}, None)
m.scan_sessions = lambda: []
m.session_names = lambda: {}
m.RollingUsage.read = lambda self: []
m.configure_display_scale()
app = QApplication([])
pet = m.DesktopPet()
pet.show()
for timer in (pet.hover_timer, pet.sessions_timer, pet.quota_timer, pet.tick_timer):
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
    assert abs(pet.carry_target_angle) <= 12
    pet.advance_carry(pet.carry_updated_at+.016)
    assert abs(pet.carry_angle) <= 12
    pet.grab()  # Paint the actual transformed pixmap, including wraparound frames.

pet.panel.reveal()
pet.handle_pointer(pet.toolbar.capsule_global_rect().center(), time.monotonic())
assert pet.panel.transition == 'closing' or not pet.panel.isVisible()
for size in (80, 96, 112, 128, 160):
    pet.change_size(size)
    for hand in (-1, 1):
        pet.carry_hand = hand
        for angle in (-12, 0, 12):
            pet.carry_angle = angle
            for phase in range(32):
                pet.carry_phase = phase*math.tau/32
                sprite = pet.sprite_global_rect().translated(-pet.pos())
                assert pet.rect().contains(sprite), (size, hand, angle, phase, sprite, pet.rect())
                assert sprite.bottom() < pet.toolbar.y(), (size, hand, angle, phase, sprite, pet.toolbar.y())
                delta = pet.sprite_transform().map(pet.carry_anchor())-pet.carry_anchor()
                assert abs(delta.x())+abs(delta.y()) < 1e-7
pet.carry_angle, pet.carry_target_angle = 12, 0
for _ in range(180):
    pet.advance_carry(pet.carry_updated_at+.016)
assert abs(pet.carry_angle) < .01, 'Sway should settle when the pointer stops'
before = pet.sprite_transform()
pet.advance_carry(pet.carry_updated_at+.05)
assert pet.sprite_transform() != before, 'A still pointer should keep the gentle struggle moving'
pet.animate = False
assert pet.animation_frame() == (m.CARRY_ROW, 2)
mouse(QEvent.Type.MouseButtonRelease, pointer, Qt.MouseButton.RightButton)
assert pet.carrying  # An unrelated button cannot terminate the left-button drag.
mouse(QEvent.Type.MouseButtonRelease, pointer, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
assert not pet.carrying and pet.drag_origin is None
assert pet.carry_angle == 0 and pet.mood == 4
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
QTest.qWait(220)
pet.panel.hide()
pet.speech.hide()
pet.hide()
app.quit()
print('PASS: 10 fps; click threshold; nearest hand follows pointer; stable grip; continuous gentle struggle with stationary pointer; both hands and all sizes stay unclipped; panel stays closed; release restores normal states')
