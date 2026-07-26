# PyInstaller spec for the single-file Windows binary.
#
#   pyinstaller packaging/watchtower.spec
#
# Two things need help that PyInstaller cannot work out on its own:
#
# * Textual ships .tcss stylesheets and a pile of dynamically imported widget
#   modules, none of which look like imports to a static analyser.
# * keyring finds its backends through entry points, so the distribution
#   metadata has to come along or every backend silently disappears and the
#   app falls back to the encrypted vault on a machine that has a keychain.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

datas = [
    (str(ROOT / "src" / "watchtower_tui" / "tui" / "app.tcss"), "watchtower_tui/tui"),
    # The rendered Bootstrap Icons. Without these the binary has no icons to
    # draw and quietly falls back to the dot marks.
    (str(ROOT / "src" / "watchtower_tui" / "assets" / "openai.png"), "watchtower_tui/assets"),
    (str(ROOT / "src" / "watchtower_tui" / "assets" / "claude.png"), "watchtower_tui/assets"),
]
binaries = []
hiddenimports = []

for package in ("textual", "rich", "textual_image", "PIL"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# keyring resolves backends via importlib.metadata entry points.
datas += copy_metadata("keyring")
hiddenimports += [
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
]

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here needs a GUI toolkit or a test runner bundled with it.
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide6", "matplotlib", "numpy", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Watchtower",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip a lot of antivirus heuristics
    runtime_tmpdir=None,
    console=True,  # it is a terminal application; a console is the point
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(SPEC_DIR / "version_info.txt"),
)
