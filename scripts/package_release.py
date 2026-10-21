"""Build portable ZIPs; preserve executable mode for macOS launchers."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
root = Path(__file__).resolve().parent.parent
asset = os.environ['ASSET']
folder = root / 'build' / ('whack-a-claude-' + asset)
folder.mkdir(parents=True, exist_ok=True)
binary = 'whack-a-claude.exe' if asset.startswith('windows') else 'whack-a-claude'
shutil.copy2(root / 'dist' / binary, folder / binary)
for name in ('README.md', 'LICENSE', 'CHANGELOG.md'):
    shutil.copy2(root / name, folder / name)
if asset.startswith('windows'):
    (folder / 'Start.bat').write_bytes(b'@echo off\r\nchcp 65001 >nul\r\n"%~dp0whack-a-claude.exe" %*\r\nif errorlevel 1 pause\r\n')
else:
    shutil.copy2(root / 'Start.command', folder / 'Start.command')
    (folder / 'Start.command').chmod(0o755)
    (folder / binary).chmod(0o755)
out = root / 'release'
out.mkdir(exist_ok=True)
archive = out / (folder.name + '.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
    for path in folder.iterdir():
        zipped.write(path, folder.name + '/' + path.name)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(out / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
print(archive)
