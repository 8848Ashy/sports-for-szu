"""Build a portable directory. Never bundle the user's local state or account."""
from pathlib import Path
import subprocess
import sys
import shutil
import importlib.metadata
import os

root = Path(__file__).resolve().parents[1]
output = Path.home() / 'Downloads' / 'SportsForSZU-v0.3.0'
work = Path(os.environ['LOCALAPPDATA']) / 'SZU-Build'
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
    '--distpath', str(output), '--workpath', str(work),
    str(root / 'tools/sports.spec')], cwd=root, check=True)
target = output / 'SportsForSZU'
shutil.copy2(root / 'PORTABLE.md', target / '使用说明.md')
shutil.copy2(root / 'THIRD_PARTY.md', target / '第三方组件说明.md')
for distribution in importlib.metadata.distributions():
    for entry in distribution.files or []:
        if 'licenses' in entry.parts or entry.name.lower().startswith(('license', 'copying', 'notice')):
            source = Path(distribution.locate_file(entry))
            if source.is_file() and source.suffix.lower() not in ('.py', '.pyc'):
                relative = Path(*[part for part in entry.parts if part not in ('..', '.')])
                destination = target / 'licenses' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
print('Portable executable:', target / 'SportsForSZU.exe')
