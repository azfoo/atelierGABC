#!/usr/bin/env python3
"""
Build script for Atelier GABC.

Steps:
  1. Reads the version from pyproject.toml
  2. Runs PyInstaller to produce dist/AtelierGABC/
  3. Runs `vpk pack` to create the Velopack release in releases/

Usage:
  python build.py

Prerequisites:
  pip install ".[build]"            # installs flask, jinja2, velopack, pyinstaller
  dotnet tool install -g vpk        # Velopack CLI packager
"""

import subprocess
import sys
import shutil
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # pip install tomli on Python < 3.11


ROOT = Path(__file__).parent


def get_version() -> str:
    with open(ROOT / 'pyproject.toml', 'rb') as f:
        return tomllib.load(f)['project']['version']


def run(*cmd, **kwargs):
    print(f'\n$ {" ".join(str(c) for c in cmd)}')
    subprocess.run(cmd, check=True, **kwargs)


def main():
    version = get_version()
    print(f'Building Atelier GABC v{version}')

    # 1. Clean previous build
    for d in ('dist', 'build'):
        shutil.rmtree(ROOT / d, ignore_errors=True)

    # 2. PyInstaller
    run(sys.executable, '-m', 'PyInstaller', '--clean', 'atelier-gabc.spec', cwd=ROOT)

    # 3. Determine the pack directory for vpk.
    #    macOS: vpk expects the .app bundle produced by BUNDLE in the spec.
    #    Windows/Linux: vpk expects the flat COLLECT directory.
    import platform
    plat = platform.system()
    if plat == 'Darwin':
        pack_dir = ROOT / 'dist' / 'Atelier GABC.app'
        main_exe = 'AtelierGABC'
    elif plat == 'Windows':
        pack_dir = ROOT / 'dist' / 'AtelierGABC'
        main_exe = 'AtelierGABC.exe'
    else:
        pack_dir = ROOT / 'dist' / 'AtelierGABC'
        main_exe = 'AtelierGABC'

    releases_dir = ROOT / 'releases'
    releases_dir.mkdir(exist_ok=True)

    # 4. Velopack pack
    run(
        'vpk', 'pack',
        '--packId',      'AtelierGABC',
        '--packVersion', version,
        '--packDir',     str(pack_dir),
        '--mainExe',     main_exe,
        '--outputDir',   str(releases_dir),
        cwd=ROOT,
    )

    print(f'\nDone — release files in releases/')
    print(f'Upload the contents of releases/ to your GitHub release for v{version}.')


if __name__ == '__main__':
    main()
