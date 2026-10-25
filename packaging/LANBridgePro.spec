# Run from the project root: python -m PyInstaller --clean --noconfirm packaging/LANBridgePro.spec
from pathlib import Path

project = Path(SPECPATH).resolve().parent
resources = project / 'src/lanbridge/resources'
analysis = Analysis(
    [str(project / 'src/lanbridge/__main__.py')],
    pathex=[str(project / 'src')],
    binaries=[], datas=[(str(resources), 'lanbridge/resources')],
    hiddenimports=['win32cred', 'win32wnet', 'win32net', 'win32netcon', 'win32api', 'win32con',
                   'win32file', 'win32pipe', 'win32event', 'win32job', 'win32process',
                   'win32security', 'pywintypes', 'win32com.shell.shell', 'win32com.shell.shellcon'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='LANBridgePro',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(resources / 'app.ico'), version=str(project / 'packaging/version_info.txt'),
          manifest=str(project / 'packaging/LANBridgePro.manifest'))
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='LANBridgePro')
