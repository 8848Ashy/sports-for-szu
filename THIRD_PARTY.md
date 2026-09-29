# 第三方组件

本软件使用 Python、PySide6/Qt（包括 Qt WebEngine）、Playwright、Requests、Pillow 等组件。
第三方组件保留各自的许可证；本项目的非商业使用条款不限制这些组件原许可证赋予的权利。

- Qt / PySide6：LGPLv3/GPLv3/商业多许可证，具体模块见 https://doc.qt.io/qt-6/licensing.html
- Qt WebEngine 所含 Chromium 及其他组件：https://doc.qt.io/qt-6/qtwebengine-licensing.html
- PySide6 源码及对应版本：https://code.qt.io/cgit/pyside/pyside-setup.git/
- Qt 源码：https://download.qt.io/archive/qt/
- Python：https://docs.python.org/3/license.html
- Playwright：https://github.com/microsoft/playwright-python
- Requests：https://github.com/psf/requests
- Pillow：https://github.com/python-pillow/Pillow

便携版采用目录式分发，Qt 动态库未静态链接。请保留第三方许可证和动态库；用户可按适用许可证替换这些库及调试修改，发布者不得以本项目条款取消相应权利。
对外分发前仍应核对构建中各具体组件的许可证、版权声明及源代码提供义务。
