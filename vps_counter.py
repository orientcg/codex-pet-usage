"""Persistent Linux interface counters; no changes to network or proxy services."""
import calendar
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def month(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m')


def next_month(stamp):
    value = datetime.fromtimestamp(stamp, timezone.utc)
    year, number = (value.year+1, 1) if value.month == 12 else (value.year, value.month+1)
    return calendar.timegm((year, number, 1, 0, 0, 0))


def accumulate(previous, sample):
    """Count deltas, recover after reboots and proportion gaps across UTC months."""
    now, rx, tx = sample['timestamp'], sample['rx'], sample['tx']
    periods = dict((previous or {}).get('periods', {}))
    incomplete = bool((previous or {}).get('incomplete', False))
    if previous is None:
        # Only seed boot counters if this boot belongs entirely to this month.
        same_month = month(now-sample['uptime']) == month(now)
        delta_rx, delta_tx = (rx, tx) if same_month else (0, 0)
        incomplete = not same_month
        start = now
    else:
        if now < previous['timestamp']:
            raise ValueError('Server clock moved backwards')
        same_boot = previous['boot_id'] == sample['boot_id']
        delta_rx = rx-previous['rx'] if same_boot and rx >= previous['rx'] else rx
        delta_tx = tx-previous['tx'] if same_boot and tx >= previous['tx'] else tx
        incomplete |= not same_boot or rx < previous['rx'] or tx < previous['tx']
        start = previous['timestamp']
    cursor, remaining_rx, remaining_tx = start, delta_rx, delta_tx
    while month(cursor) != month(now):
        boundary = next_month(cursor)
        fraction = (boundary-cursor)/(now-cursor)
        take_rx, take_tx = round(remaining_rx*fraction), round(remaining_tx*fraction)
        item = dict(periods.get(month(cursor), {'rx': 0, 'tx': 0}))
        item['rx'] += take_rx
        item['tx'] += take_tx
        periods[month(cursor)] = item
        remaining_rx -= take_rx
        remaining_tx -= take_tx
        cursor = boundary
    item = dict(periods.get(month(now), {'rx': 0, 'tx': 0}))
    item['rx'] += remaining_rx
    item['tx'] += remaining_tx
    periods[month(now)] = item
    periods = {key: periods[key] for key in sorted(periods)[-24:]}
    return dict(sample, periods=periods, incomplete=incomplete)


def collect(folder=Path('/var/lib/codex-pet-traffic')):
    import fcntl
    config = json.loads((folder/'config.json').read_text())
    interface = config['interface']
    with (folder/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counters = Path('/sys/class/net')/interface/'statistics'
        sample = {'timestamp': time.time(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'uptime': float(Path('/proc/uptime').read_text().split()[0]),
                  'rx': int((counters/'rx_bytes').read_text()), 'tx': int((counters/'tx_bytes').read_text())}
        state_path = folder/'state.json'
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
        state = accumulate(previous, sample)
        current = state['periods'][month(sample['timestamp'])]
        output = {'version': 1, 'period': month(sample['timestamp']), 'rx_bytes': current['rx'],
                  'tx_bytes': current['tx'], 'used_bytes': current['rx']+current['tx'],
                  'observed_at': sample['timestamp'], 'interface': interface, 'timezone': 'UTC',
                  'source': 'network-interface', 'incomplete': state['incomplete']}
        for name, data in (('state.json', state), ('current.json', output)):
            temporary = folder/(name+'.tmp')
            temporary.write_text(json.dumps(data), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(folder/name)
        return output


if __name__ == '__main__':
    print(json.dumps(collect()))
