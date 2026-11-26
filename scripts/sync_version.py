#!/usr/bin/env python3
"""Generate shipped version markers from VERSION; --check refuses drift."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def sync(check=False):
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be major.minor.patch')
    markers = {
        'SKILL.md': (r'(?m)^version: .*$', f'version: {version}'),
        'assets/novel-workbench.html': (
            r'<meta name="workbench-version" content="[^"]*">',
            f'<meta name="workbench-version" content="{version}">'),
    }
    for name, (pattern, marker) in markers.items():
        path = ROOT / name
        old = path.read_text(encoding='utf-8')
        new, count = re.subn(pattern, lambda _: marker, old)
        if count != 1:
            raise ValueError(f'Missing or duplicate version marker: {name}')
        if check and old != new:
            raise ValueError(f'Version drift: {name}; run python3 scripts/sync_version.py')
        if not check and old != new:
            path.write_text(new, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
