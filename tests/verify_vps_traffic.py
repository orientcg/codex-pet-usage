"""Counter/accounting and pinned read-only SSH checks; never contact a real VPS."""
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vps_counter import accumulate
from vps_traffic import compact_bytes, fetch_vps_traffic, parse_traffic
from install_vps_traffic import REMOTE_INSTALL


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def sample(when, rx, tx, boot='one', uptime=3600):
    return dict(timestamp=stamp(when), rx=rx, tx=tx, boot_id=boot, uptime=uptime)


a = sample('2026-10-27T12:00:00', 100, 50)
state = accumulate(None, a)
assert state['periods']['2026-10'] == {'rx': 100, 'tx': 50}
state = accumulate(state, sample('2026-10-27T12:01:00', 120, 60))
assert state['periods']['2026-10'] == {'rx': 120, 'tx': 60}
assert accumulate(state, sample('2026-10-27T12:01:00', 120, 60)) == state
state = accumulate(state, sample('2026-10-27T12:02:00', 7, 9, boot='two'))
assert state['periods']['2026-10'] == {'rx': 127, 'tx': 69} and state['incomplete']
state = accumulate(state, sample('2026-10-27T12:03:00', 3, 1, boot='two'))
assert state['periods']['2026-10'] == {'rx': 130, 'tx': 70}
try:
    accumulate(state, a)
    raise AssertionError('Clock regression must fail without resetting counts')
except ValueError:
    pass
before = accumulate(None, sample('2026-10-31T23:59:30', 100, 50))
after = accumulate(before, sample('2026-11-01T00:00:30', 160, 80))
assert after['periods']['2026-10'] == {'rx': 130, 'tx': 65}
assert after['periods']['2026-11'] == {'rx': 30, 'tx': 15}
long_gap = accumulate(after, sample('2027-01-02T00:00:00', 1000, 500))
assert sum(item['rx']+item['tx'] for item in long_gap['periods'].values()) == 1500
old_boot = accumulate(None, sample('2026-11-01T00:01:00', 1000, 500, uptime=86400))
assert old_boot['incomplete'] and old_boot['periods']['2026-11'] == {'rx': 0, 'tx': 0}

now = time.time()
snapshot = {'version': 1, 'period': '2026-10', 'rx_bytes': 40, 'tx_bytes': 60,
            'used_bytes': 100, 'observed_at': now}
assert parse_traffic(snapshot, 1000, now)['used_percent'] == 10
assert parse_traffic(snapshot, 50, now)['used_percent'] == 200
for changes in ({'used_bytes': -1}, {'rx_bytes': True}, {'used_bytes': 101},
                {'observed_at': now-181}, {'observed_at': float('nan')}, {'period': '2026-13'}):
    try:
        parse_traffic(dict(snapshot, **changes), 1000, now)
        raise AssertionError(changes)
    except ValueError:
        pass
assert [compact_bytes(v) for v in (None, 0, 1000, 1500000, 12000000000, 1000000000000)] == ['—', '0B', '1K', '1.5M', '12G', '1T']
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    config = root/'vps.json'
    config.write_text(json.dumps({'host': 'example.invalid', 'port': 22, 'identity_file': str(root/'key'),
                                  'known_hosts': str(root/'pinned'), 'limit_bytes': 1000}), encoding='utf-8')
    with patch('vps_traffic.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(snapshot), '')) as run:
        assert fetch_vps_traffic(config)['used_percent'] == 10
        args = run.call_args.args[0]
        assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args and 'IdentitiesOnly=yes' in args
        assert args[-2:] == ['root@example.invalid', 'read-traffic']
        assert '-i' in args and not any('password' in str(a).lower() for a in args)
    with patch('vps_traffic.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'private details')):
        try:
            fetch_vps_traffic(config)
            raise AssertionError('Failed SSH must not report zero usage')
        except RuntimeError as error:
            assert 'private details' not in str(error)
assert fetch_vps_traffic(Path(tempfile.gettempdir())/'absent-codex-pet-vps-config.json') is None
compile(REMOTE_INSTALL, '<remote installer>', 'exec')
assert 'restrict,command=' in REMOTE_INSTALL
print('PASS: counter seeding/deltas; repeated samples; reboots/resets; month/year boundaries; long gaps; incomplete history; stale/invalid snapshots; byte labels; pinned noninteractive read-only SSH; no fake zero on failure')
