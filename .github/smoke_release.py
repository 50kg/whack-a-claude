"""Verify the ZIP users actually download, including Chinese menu output."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from whack_a_claude import VERSION
archive = next((root / '.build/release').glob('*.zip'))
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
    assert result.stdout.strip() == VERSION, result.stdout
    # Only open/exit the menu; never run maintenance on runner data.
    result = subprocess.run([str(binary)], input='0\n', capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    assert '完整备份' in result.stdout and '\ufffd' not in result.stdout, result.stdout
    assert '清理（不自动备份）' in result.stdout and '重建会话列表' in result.stdout, result.stdout
    assert '安装' not in result.stdout and '一把梭' not in result.stdout, result.stdout
print('Extracted standalone package: version and Chinese menu passed.')
