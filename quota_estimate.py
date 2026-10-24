"""Estimate remaining requests from observed quota changes, without a fixed cap."""
from datetime import datetime
import math
import time


def rate_limit_sample(record):
    if record.get('type') != 'event_msg':
        return None
    payload = record.get('payload') or {}
    if payload.get('type') != 'token_count':
        return None
    limits = payload.get('rate_limits') or {}
    primary = limits.get('primary') or {}
    used = primary.get('used_percent')
    if not isinstance(used, (int, float)) or isinstance(used, bool) or not math.isfinite(used) or not 0 <= used <= 100:
        return None
    try:
        return {'timestamp': datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).timestamp(),
                'limit_id': limits.get('limit_id') or 'codex', 'used_percent': used,
                'window_minutes': primary.get('window_minutes'), 'resets_at': int(primary['resets_at'])}
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        return None


def estimate_remaining_requests(records, samples, limits, limit_id, now=None):
    now = time.time() if now is None else now
    primary = limits.get('primary') or {}
    used = primary.get('usedPercent')
    reset = primary.get('resetsAt')
    if (primary.get('windowDurationMins') != 300 or not isinstance(used, (int, float))
            or isinstance(used, bool) or not math.isfinite(used) or not 0 <= used <= 100
            or not isinstance(reset, (int, float)) or reset <= now):
        return None
    remaining = max(0., 100-used)
    if remaining == 0:
        return {'count': 0, 'requests': 0, 'consumed_percent': 0, 'resets_at': int(reset)}
    observations = sorted((s for s in samples if s['limit_id'] == limit_id and s['window_minutes'] == 300
                           and s['resets_at'] == int(reset) and now-3600 <= s['timestamp'] <= now),
                          key=lambda s: s['timestamp'])
    # A decrease invalidates the old trend, even when the server reset id stays.
    start_index = 0
    for i in range(1, len(observations)):
        if observations[i]['used_percent'] < observations[i-1]['used_percent']:
            start_index = i
    observations = observations[start_index:]
    if len(observations) < 2:
        return None
    first, last = observations[0], observations[-1]
    consumed = last['used_percent']-first['used_percent']
    requests = sum(first['timestamp'] < stamp <= last['timestamp'] for stamp, _ in records.values())
    # Percentages are quantized. A tiny or single-request change is too noisy.
    if consumed < 1 or requests < 3 or last['timestamp']-first['timestamp'] < 30:
        return None
    return {'count': math.floor(remaining*requests/consumed), 'requests': requests,
            'consumed_percent': consumed, 'resets_at': int(reset)}
