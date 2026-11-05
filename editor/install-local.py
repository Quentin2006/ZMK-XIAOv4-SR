#!/usr/bin/env python3
"""Install the loopback-only control panel as this user's login service."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, check=True)


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    gh = shutil.which('gh')
    if not gh:
        raise SystemExit('Install GitHub CLI and run gh auth login before installing the panel.')
    # Reuse the existing OS-keyring sign-in. No credential is copied into the app.
    subprocess.run([gh, 'auth', 'status', '--hostname', 'github.com'], check=True, capture_output=True)
    helper = '!' + shlex.quote(gh) + ' auth git-credential'
    run('git', '-C', str(ROOT), 'config', '--local', '--replace-all', 'credential.https://github.com.helper', helper)
    service = Path.home() / '.config/systemd/user/skree-control.service'
    service.parent.mkdir(parents=True, exist_ok=True)
    # Keep the compositor connection available for the explicit Apply action.
    variables = [name for name in ['HYPRLAND_INSTANCE_SIGNATURE', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'SSH_AUTH_SOCK'] if name in os.environ]
    if variables:
        run('systemctl', '--user', 'import-environment', *variables)
    body = '\n'.join([
        '[Unit]', 'Description=Skree keyboard control panel (localhost)',
        'After=graphical-session.target', '', '[Service]', 'Type=simple',
        'WorkingDirectory=' + str(ROOT).replace('%', '%%'),
        'ExecStart=' + unit_quote(sys.executable) + ' ' + unit_quote(ROOT / 'editor/server.py'),
        'Environment=' + unit_quote('PATH=' + str(Path(gh).parent) + ':/usr/local/bin:/usr/bin:/bin'),
        'Restart=on-failure', 'RestartSec=3', 'UMask=0077', '',
        '[Install]', 'WantedBy=default.target', ''
    ])
    if service.exists() and service.read_text() != body:
        backup = service.with_name(service.name + '.bak.' + str(time.time_ns()))
        shutil.copyfile(service, backup)
    service.write_text(body)
    run('systemctl', '--user', 'daemon-reload')
    run('systemctl', '--user', 'enable', 'skree-control.service')
    run('systemctl', '--user', 'restart', 'skree-control.service')
    print('Open http://127.0.0.1:8766 — Skree Control starts with your user session.')


if __name__ == '__main__':
    main()
