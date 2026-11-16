# PyInstaller spec for winphone.exe: one folder (starts fast; a one-file exe unpacks itself on every run).
# Build with: python -m uv run python packaging/build.py
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")  # uvicorn picks its protocol/loop modules at run time
    + collect_submodules("winphone_core")
    + collect_submodules("winphone_android")
    + collect_submodules("winphone_ios")
    + collect_submodules("winphone_mcp")
    + collect_submodules("winphone_cli")
)
datas = collect_data_files("uiautomator2")  # u2.jar, pushed to Android phones
datas += [("../skills/claude-code/winphone/SKILL.md", "skills/claude-code/winphone")]

a = Analysis(["winphone_entry.py"], pathex=[], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "IPython", "PIL.ImageQt"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="winphone", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="winphone", upx=False)
