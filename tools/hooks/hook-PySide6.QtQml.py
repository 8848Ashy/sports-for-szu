"""Widgets/WebEngine needs QtQml DLLs, not the unrelated full QML module tree."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
