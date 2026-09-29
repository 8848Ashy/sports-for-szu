# Reproducible Windows directory build. Qt uses the OS ICU ABI on this platform.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'run.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'sports_szu/web'), 'sports_szu/web'),
           (str(root / 'sports_szu/assets'), 'sports_szu/assets')],
    hiddenimports=[],
    hookspath=[str(root / 'tools/hooks')],
    excludes=['tkinter', 'pystray'],
    noarchive=False,
)
# A PATH-provided ICU (e.g. a compiler toolchain) has versioned exports, unlike
# Windows System32 ICU expected by this Qt build. Do not shadow the OS DLL.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SportsForSZU',
          console=False, debug=False, upx=False,
          icon=str(root / 'sports_szu/assets/noodles.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SportsForSZU')
