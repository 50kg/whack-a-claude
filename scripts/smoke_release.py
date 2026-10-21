"""Verify the ZIP users actually download, including Chinese menu output."""
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

root = Path(__file__).resolve().parent.parent
archive = next((root / 'release').glob('*.zip'))
with tempfile.TemporaryDirectory(prefix='whack-package-') as temp:
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(temp)
        for info in zipped.infolist():
            mode = info.external_attr >> 16
            if mode:
                (Path(temp) / info.filename).chmod(mode)
    folder = next(Path(temp).iterdir())
    binary = folder / ('whack-a-claude.exe' if os.name == 'nt' else 'whack-a-claude')
    result = subprocess.run([str(binary), '--version'], capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    assert result.stdout.strip() == '2.0.0', result.stdout
    # Only open/exit the menu; never run maintenance on runner data.
    result = subprocess.run([str(binary)], input='0\n', capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    assert '完整备份' in result.stdout and '\ufffd' not in result.stdout, result.stdout
print('Extracted standalone package: version and Chinese menu passed.')
