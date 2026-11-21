#!/usr/bin/env python3
"""One-time system DPI declaration installer; never invoked by the web server."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    config = model.validate(json.loads((model.ROOT / model.CONFIG).read_text()))
    body = model.render_hwdb(config)
    source = model.ROOT / model.HWDB
    model.require(source.read_text() == body, 'Regenerate the tracked hwdb source before installing.')
    target = Path('/etc/udev/hwdb.d/99-skree-control.hwdb')
    if args.dry_run:
        print(str(target) + '\n' + body)
        return
    model.require(os.geteuid() == 0, 'Run this installer with pkexec or sudo.')
    original = target.read_bytes() if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    if original is not None:
        backup = target.with_name(target.name + '.bak.' + str(time.time_ns()))
        shutil.copyfile(target, backup)
    try:
        temporary = target.with_name(target.name + '.tmp')
        temporary.write_text(body)
        temporary.chmod(0o644)
        temporary.replace(target)
        subprocess.run(['/usr/bin/systemd-hwdb', 'update'], check=True)
        # Update properties only for the Skree pointer nodes. Reconnecting the
        # device afterwards lets libinput open it with the new declaration.
        for event in Path('/sys/class/input').glob('event*'):
            device = event / 'device'
            try:
                vendor = (device / 'id/vendor').read_text().strip()
                product = (device / 'id/product').read_text().strip()
                name = (device / 'name').read_text().strip()
            except OSError:
                continue
            if vendor == '1d50' and product == '615e' and 'Skree Custom' in name:
                subprocess.run(['/usr/bin/udevadm', 'trigger', '--action=change', str(event.resolve())], check=True)
        subprocess.run(['/usr/bin/udevadm', 'settle'], check=True)
    except Exception:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(original)
        subprocess.run(['/usr/bin/systemd-hwdb', 'update'], check=False)
        raise
    print('Installed Skree-only DPI declaration. Reconnect the keyboard to activate it in libinput.')


if __name__ == '__main__':
    main()
