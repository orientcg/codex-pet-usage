"""Read one traffic snapshot over a host-pinned, command-restricted SSH key."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

BASE = Path(__file__).resolve().parent


def parse_traffic(data, limit_bytes, now=None):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported traffic snapshot')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(data.get('period', ''))):
        raise ValueError('Invalid traffic period')
    for key in ('rx_bytes', 'tx_bytes', 'used_bytes'):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('Invalid byte count')
    if data['used_bytes'] != data['rx_bytes']+data['tx_bytes']:
        raise ValueError('Inconsistent traffic counters')
    if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes <= 0:
        raise ValueError('Invalid traffic allowance')
    now = time.time() if now is None else now
    stamp = data.get('observed_at')
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or not -60 <= now-stamp <= 180:
        raise ValueError('Traffic snapshot is stale')
    return dict(data, limit_bytes=limit_bytes, used_percent=data['used_bytes']/limit_bytes*100,
                fetched_at=now)


def compact_bytes(value):
    if value is None:
        return '—'
    units = ('B', 'K', 'M', 'G', 'T', 'P')
    size = float(value)
    index = 0
    while size >= 1000 and index < len(units)-1:
        size /= 1000
        index += 1
    text = f'{size:.1f}'.rstrip('0').rstrip('.') if size < 10 else f'{size:.0f}'
    if text == '1000' and index < len(units)-1:
        text, index = '1', index+1
    return text+units[index]


def fetch_vps_traffic(config_path=None):
    path = Path(config_path) if config_path else BASE/'vps.json'
    if not path.exists():
        return None
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    host, user = config['host'], config.get('user', 'root')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
        raise ValueError('Invalid SSH address')
    port = config.get('port', 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    args = ['ssh.exe' if os.name == 'nt' else 'ssh', '-T', '-p', str(port),
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=1',
            '-o', 'UserKnownHostsFile='+str(Path(config['known_hosts']).resolve()),
            '-i', str(Path(config['identity_file']).resolve())]
    interface = config.get('local_interface')
    if interface:
        # Resolve a changed Windows interface index after reconnecting Wi-Fi.
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
            'Get-NetAdapter | Where-Object Name -eq $env:CODEX_PET_INTERFACE | Select-Object -ExpandProperty ifIndex'],
            env=dict(os.environ, CODEX_PET_INTERFACE=interface), capture_output=True, text=True,
            timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        index = int(result.stdout.strip())
        proxy = subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), 'bridge',
                                        '--host', host, '--port', str(port), '--ifindex', str(index)])
        args += ['-o', 'ProxyCommand='+proxy]
    args += [user+'@'+host, 'read-traffic']
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=22,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('VPS traffic connection failed')
    if len(result.stdout) > 16384:
        raise ValueError('Oversized traffic snapshot')
    return parse_traffic(json.loads(result.stdout), config['limit_bytes'])


def bridge(host, port, index):
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(index))
    sock.settimeout(12)
    sock.connect((host, port))
    sock.settimeout(None)

    def upload():
        try:
            while data := os.read(sys.stdin.fileno(), 65536):
                sock.sendall(data)
        except OSError:
            pass
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    threading.Thread(target=upload, daemon=True).start()
    try:
        while data := sock.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    finally:
        sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['bridge', 'check'])
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--ifindex', type=int)
    args = parser.parse_args()
    if args.mode == 'bridge':
        bridge(args.host, args.port, args.ifindex)
    else:
        snapshot = fetch_vps_traffic()
        print(json.dumps(snapshot, ensure_ascii=False))
