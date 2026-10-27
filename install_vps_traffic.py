"""Install an isolated traffic collector and a command-restricted SSH key."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parent

REMOTE_INSTALL = r"""
import base64,json,os,pathlib,re,shutil,subprocess,time
assert os.geteuid()==0, 'Install as root'
settings=json.loads(SETTINGS)
public=settings['public_key']
assert re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\n\r]*)?',public)
assert re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}',settings['interface'])
data=pathlib.Path('/var/lib/codex-pet-traffic')
code=pathlib.Path('/usr/local/lib/codex-pet-traffic')
for directory in (data,code):
    assert not directory.is_symlink()
    directory.mkdir(parents=True,exist_ok=True)
data.chmod(0o700)
stamp=str(int(time.time()))
def write(path,content,mode):
    assert not path.is_symlink()
    if path.exists() and path.read_text()!=content:
        shutil.copy2(path,data/(path.name+'.before-'+stamp))
    temporary=path.with_name(path.name+'.codex-pet.tmp')
    temporary.write_text(content)
    temporary.chmod(mode)
    temporary.replace(path)
write(code/'vps_counter.py',settings['source'],0o644)
write(data/'config.json',json.dumps({'interface':settings['interface']}),0o600)
service='''[Unit]
Description=Codex Pet interface traffic collector
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/codex-pet-traffic/vps_counter.py
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/codex-pet-traffic
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX
'''
timer='''[Unit]
Description=Collect Codex Pet traffic once per minute
[Timer]
OnBootSec=15s
OnCalendar=*-*-* *:*:00
AccuracySec=1s
Persistent=true
[Install]
WantedBy=timers.target
'''
write(pathlib.Path('/etc/systemd/system/codex-pet-traffic.service'),service,0o644)
write(pathlib.Path('/etc/systemd/system/codex-pet-traffic.timer'),timer,0o644)
ssh=pathlib.Path('/root/.ssh')
assert not ssh.is_symlink()
ssh.mkdir(exist_ok=True)
ssh.chmod(0o700)
keys=ssh/'authorized_keys'
old=keys.read_text() if keys.exists() else ''
line='restrict,command="/usr/bin/cat /var/lib/codex-pet-traffic/current.json" '+public
existing=[entry for entry in old.splitlines() if public.split()[1] in entry.split()]
assert not existing or all(entry==line for entry in existing), 'Existing traffic key is not command-restricted'
if not existing:
    write(keys,old.rstrip('\n')+'\n'+line+'\n',0o600)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start','codex-pet-traffic.service'],check=True)
subprocess.run(['systemctl','enable','--now','codex-pet-traffic.timer'],check=True,capture_output=True)
snapshot=json.loads((data/'current.json').read_text())
print(json.dumps({'installed':True,'snapshot':snapshot,'timer':subprocess.check_output(['systemctl','is-active','codex-pet-traffic.timer'],text=True).strip()}))
"""


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        sid = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
            '[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value'], text=True).strip()
        subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
                       check=True, capture_output=True)
    else:
        path.chmod(0o700)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--known-hosts', type=Path, required=True)
    parser.add_argument('--interface', default='eth0')
    parser.add_argument('--local-interface')
    parser.add_argument('--limit-gb', type=int, required=True, help='Decimal GB per UTC calendar month')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', args.host) or not 1 <= args.port <= 65535 or args.limit_gb <= 0:
        parser.error('Invalid address or traffic allowance')
    private = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.local/share')))/'CodexPetUsage'/'private'
    private_directory(private)
    key = private/'traffic_ed25519'
    if not key.exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key),
                        '-C', 'codex-pet-read-traffic'], check=True)
    public = key.with_suffix('.pub').read_text().strip()
    pinned = private/'traffic_known_hosts'
    shutil.copyfile(args.known_hosts.resolve(), pinned)
    settings = json.dumps({'public_key': public, 'interface': args.interface,
                          'source': (ROOT/'vps_counter.py').read_text(encoding='utf-8')})
    installer = 'SETTINGS='+repr(settings)+'\n'+REMOTE_INSTALL
    encoded = base64.b64encode(zlib.compress(installer.encode())).decode()
    import shlex
    command = 'python3 -c '+shlex.quote("import base64,zlib;exec(zlib.decompress(base64.b64decode('"+encoded+"')))")
    ssh = ['ssh.exe' if os.name == 'nt' else 'ssh', '-T', '-p', str(args.port),
           '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(pinned),
           '-o', 'ConnectTimeout=12', '-o', 'ConnectionAttempts=1',
           '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1']
    if args.local_interface:
        result = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
            'Get-NetAdapter | Where-Object Name -eq $env:CODEX_PET_INTERFACE | Select-Object -ExpandProperty ifIndex'],
            env=dict(os.environ, CODEX_PET_INTERFACE=args.local_interface), text=True).strip()
        proxy = subprocess.list2cmdline([sys.executable, str(ROOT/'vps_traffic.py'), 'bridge',
            '--host', args.host, '--port', str(args.port), '--ifindex', str(int(result))])
        ssh += ['-o', 'ProxyCommand='+proxy]
    subprocess.run(ssh+['root@'+args.host, command], check=True)
    config = {'host': args.host, 'port': args.port, 'user': 'root',
              'identity_file': str(key), 'known_hosts': str(pinned), 'limit_bytes': args.limit_gb*1000**3}
    if args.local_interface:
        config['local_interface'] = args.local_interface
    (ROOT/'vps.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    print('Configured private vps.json and restricted traffic key; no password saved.')


if __name__ == '__main__':
    main()
