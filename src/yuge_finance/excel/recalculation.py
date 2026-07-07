"""数式の再計算（喜らく単体）。

openpyxl は数式を計算しない。出力時に fullCalcOnLoad=True を立てているため
Excel / LibreOffice で開くと自動再計算される。
LibreOffice(soffice) が利用可能な場合はヘッドレス変換で値を確定できる。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def libreoffice_available() -> Optional[str]:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    # macOS の標準インストール先
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(mac) if mac.exists() else None


def recalc_with_libreoffice(workbook_path: Path) -> bool:
    """LibreOffice で開いて保存し直し、数式を実値化する。成功でTrue。"""
    soffice = libreoffice_available()
    if not soffice:
        return False
    wb = Path(workbook_path)
    try:
        subprocess.run(
            [soffice, "--headless", "--calc", "--convert-to", "xlsx",
             "--outdir", str(wb.parent), str(wb)],
            check=True, capture_output=True, timeout=120,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False
