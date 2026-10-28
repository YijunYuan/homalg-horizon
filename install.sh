#!/usr/bin/env bash
# Install a tagged HomAlg fork wheel with its compiled dashboard, never source main.
set -euo pipefail
python3 - <<'PY'
import json, os, re, shutil, subprocess, sys
from urllib.request import Request, urlopen
repo = os.environ.get('HOMALG_HORIZON_REPOSITORY', 'YijunYuan/homalg-horizon')
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
    raise SystemExit('HOMALG_HORIZON_REPOSITORY must be owner/repository')
with urlopen(Request(f'https://api.github.com/repos/{repo}/releases/latest', headers={'User-Agent':'homalg-horizon-installer'}), timeout=30) as response:
    release = json.load(response)
tag = release.get('tag_name', '')
match = re.fullmatch(r'v([0-9]+\.[0-9]+\.[0-9]+)-homalg\.([0-9]+)', tag)
if not match or release.get('draft') or release.get('prerelease'):
    raise SystemExit('No stable tagged HomAlg release found')
name = f'archon_horizon-{match[1]}+homalg.{match[2]}-py3-none-any.whl'
expected = f'https://github.com/{repo}/releases/download/{tag}/{name}'
assets = [a for a in release.get('assets', []) if a.get('name') == name]
if len(assets) != 1 or assets[0].get('browser_download_url', '').replace('%2B','+').replace('%2b','+') != expected:
    raise SystemExit('Release is missing its built wheel')
argv = ['uv', 'tool', 'install', '--force', expected] if shutil.which('uv') else [sys.executable, '-m', 'pip', 'install', '--upgrade', expected]
subprocess.run(argv, check=True)
PY
