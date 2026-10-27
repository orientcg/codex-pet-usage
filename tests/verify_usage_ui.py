"""Native UI checks with example data; no account calls or personal log reads."""
import sys
import time
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pet_desktop as m
from usage_series import local_now
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

assert not (Path(__file__).resolve().parents[1]/'pet_chat.py').exists()
assert not (Path(__file__).resolve().parents[1]/'pet_quick_chat.py').exists()
m.fetch_account = lambda: ({}, None)
m.fetch_vps_traffic = lambda: None
m.scan_sessions = lambda: []
m.session_names = lambda: {}
m.RollingUsage.read = lambda self: []
m.configure_display_scale()
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
pet = m.DesktopPet()
pet.show()
pet.hover_timer.stop()
QTest.qWait(220)
for timer in (pet.tick_timer, pet.sessions_timer, pet.quota_timer, pet.queue_timer, pet.vps_timer):
    timer.stop()
assert not hasattr(pet, 'composer') and not hasattr(pet, 'new_chat') and not hasattr(pet, 'voice_chat')
bar, panel = pet.toolbar, pet.panel
assert len(bar.item_rects()) == 2
pet.vps_traffic = {'used_bytes': 126000000000, 'used_percent': 4.2}

for index in (0, 1, 0, 1, None):
    anchor = bar.mapToGlobal(bar.item_rects()[index].center().toPoint()) if index is not None else None
    bar.set_active(index)
    bar.motion.pause()
    for moment in range(0, 221, 11):
        bar.motion.setCurrentTime(moment)
        rects = bar.item_rects()
        assert all(QRectF(bar.rect()).contains(rect) for rect in rects)
        assert rects[0].right()+7.9 <= rects[1].left()
        if anchor is not None:
            assert bar.item_at(bar.mapFromGlobal(anchor), keep_active=True) == index
    bar.motion.stop()
bar.set_active(bar.QUOTA_INDEX)
QTest.qWait(240)
quota_rect = bar.item_rects()[bar.QUOTA_INDEX]
assert quota_rect.width() == 136
assert quota_rect.left() <= quota_rect.center().x()-60 and quota_rect.right() >= quota_rect.center().x()+60
pet.grab()
pet.vps_error = True
pet.grab()
pet.messages.put(('vps_error', None))
pet.poll()
assert pet.vps_traffic['used_bytes'] == 126000000000 and pet.vps_error
pet.messages.put(('vps', {'used_bytes': 127000000000, 'used_percent': 4.3}))
pet.poll()
assert not pet.vps_error and pet.vps_traffic['used_bytes'] == 127000000000

now = local_now()
pet.limits = {'codex': {'primary': {'usedPercent': 32, 'windowDurationMins': 300, 'resetsAt': time.time()+7200},
                         'secondary': {'usedPercent': 18, 'windowDurationMins': 10080}}}
pet.today_usage = {'date': now.date().isoformat(), 'buckets': [
    {'startDate': f'{hour:02}:00', 'tokens': (hour+1)*1000, 'recorded': True} for hour in range(24)]}
pet.usage = {'dailyUsageBuckets': [
    {'startDate': (now.date()-timedelta(days=day)).isoformat(), 'tokens': (day+1)*10000} for day in range(30)]}
panel.render()
bar.set_active(None)
QTest.qWait(230)
quota = bar.mapToGlobal(bar.item_rects()[bar.QUOTA_INDEX].center().toPoint())
pet.handle_pointer(quota, 1.)
QTest.qWait(230)
pet.handle_pointer(quota, 1.3)
assert not panel.isVisible()
bar.set_active(None)
QTest.qWait(230)
running = bar.mapToGlobal(bar.item_rects()[bar.RUN_INDEX].center().toPoint())
pet.handle_pointer(running, 2.)
QTest.qWait(230)
pet.handle_pointer(running, 2.1)
assert panel.transition == 'opening'
panel.motion.pause()
for moment in range(0, 141, 10):
    panel.motion.setCurrentTime(moment)
    app.processEvents()
    assert panel.target_rect.contains(panel.geometry())
    assert all(not panel.geometry().intersects(rect) for rect in pet.protected_rects())
    assert panel.windowOpacity() == 1.
panel.motion.stop()
panel.finish_motion()
for period, count in (('30天', 30), ('7天', 7), ('今日', 24)):
    button = panel.periods[period]
    scene = panel.proxy.mapToScene(button.mapTo(panel.content, button.rect().center()))
    QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=panel.view.mapFromScene(scene))
    QTest.qWait(30)
    assert pet.period == period and len(panel.chart.bar_rects) == count
    assert panel.total.y() > panel.chart.geometry().bottom()

far = pet.pos()+QPoint(-500, -300)
pet.handle_pointer(far, 3.)
pet.handle_pointer(far, 3.08)
QTest.qWait(130)
assert not panel.isVisible()

baseline = bar.y()
path = Path('rollout-demo-00000000-0000-0000-0000-000000000001.jsonl')
pet.paths = [path]
pet.names = {path.stem[-36:]: '示例项目'}
pet.snapshots = {str(path): {'state': 'running', 'summary': '检查显示效果', 'summary_time': now.isoformat(), 'active_time': now.isoformat()}}
pet.speech.sync_activity()
QTest.qWait(230)
assert bar.y() == baseline+pet.speech.height()+8
assert pet.sprite_global_rect().bottom() < pet.speech.y()
assert pet.speech.frameGeometry().bottom() < bar.mapToGlobal(QPoint()).y()
opened = []
pet.open_url = opened.append
QTest.mouseClick(pet.speech, Qt.MouseButton.LeftButton, pos=pet.speech.rect().center())
assert opened == ['codex://threads/'+path.stem[-36:]]
area = pet.screen().availableGeometry()
for fx, fy in ((0, 0), (1, 0), (0, 1), (1, 1), (.5, .5)):
    panel.hide()
    pet.move(area.left()+round((area.width()-pet.width())*fx), area.top()+round((area.height()-pet.height())*fy))
    pet.layout_stack(animated=False)
    assert panel.place_near_pet()
    assert area.contains(panel.target_rect)
    assert all(not panel.target_rect.intersects(rect) for rect in pet.protected_rects())
QTest.mouseClick(bar, Qt.MouseButton.RightButton, pos=bar.rect().center())
assert QApplication.activePopupWidget() is None
panel.hide()
pet.speech.hide()
pet.hide()
app.quit()
print('PASS: chat absent; two entries; three quota/traffic rings; stale data retained on VPS failure; pointer retention; run-only panel; 30/7/24 bars; native summary; safe placement; no right-click settings')
