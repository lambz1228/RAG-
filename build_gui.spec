# -*- mode: python ; coding: utf-8 -*-
"""BioKB 桌面入口；运行数据和凭据不包含在可执行文件中。"""
from pathlib import Path
ROOT = Path(SPECPATH).resolve()
a = Analysis([str(ROOT / "gui.py")], pathex=[str(ROOT / "src")], binaries=[],
    datas=[(str(ROOT / "src/biokb/web/templates"), "biokb/web/templates"),
           (str(ROOT / "src/biokb/web/static"), "biokb/web/static")],
    hiddenimports=["waitress", "pdfplumber", "docx", "biokb.web.app"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="BioKB", debug=False,
          strip=False, upx=False, console=True)
