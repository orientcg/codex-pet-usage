import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quota_estimate import estimate_remaining_requests, rate_limit_sample

now, reset = 2000000000, 2000005000
limits = {'primary': {'usedPercent': 25, 'resetsAt': reset, 'windowDurationMins': 300}}
samples = [{'timestamp': now-600, 'limit_id': 'codex', 'window_minutes': 300, 'resets_at': reset, 'used_percent': 20},
           {'timestamp': now-60, 'limit_id': 'codex', 'window_minutes': 300, 'resets_at': reset, 'used_percent': 25}]
records = {f'r{i}': (now-570+i*50, 1000) for i in range(10)}
result = estimate_remaining_requests(records, samples, limits, 'codex', now)
assert result['count'] == 150 and result['requests'] == 10 and result['consumed_percent'] == 5
assert estimate_remaining_requests({}, samples, limits, 'codex', now) is None
assert estimate_remaining_requests(dict(list(records.items())[:2]), samples, limits, 'codex', now) is None
assert estimate_remaining_requests(records, samples, limits, 'other', now) is None
assert estimate_remaining_requests(records, samples, {'primary': dict(limits['primary'], resetsAt=reset+1)}, 'codex', now) is None
assert estimate_remaining_requests(records, samples, {'primary': dict(limits['primary'], windowDurationMins=1440)}, 'codex', now) is None
assert estimate_remaining_requests(records, samples, {'primary': dict(limits['primary'], usedPercent=100)}, 'codex', now)['count'] == 0
flat = [dict(s, used_percent=20) for s in samples]
assert estimate_remaining_requests(records, flat, limits, 'codex', now) is None
regressed = samples+[dict(samples[-1], timestamp=now-30, used_percent=23)]
assert estimate_remaining_requests(records, regressed, limits, 'codex', now) is None
stale = [dict(s, timestamp=s['timestamp']-4000) for s in samples]
assert estimate_remaining_requests(records, stale, limits, 'codex', now) is None
payload = {'type': 'event_msg', 'timestamp': '2026-10-24T00:00:00Z', 'payload': {'type': 'token_count',
           'rate_limits': {'limit_id': 'codex', 'primary': {'used_percent': 20, 'window_minutes': 300, 'resets_at': reset}}}}
assert rate_limit_sample(payload)['used_percent'] == 20
assert rate_limit_sample({'type': 'token_usage_record'}) is None
print('PASS: observed quota-per-request estimate; no fixed capacity; insufficient samples/flat quota/regression/reset/other bucket/stale observations excluded; exhausted quota zero; native quota log parsed')
