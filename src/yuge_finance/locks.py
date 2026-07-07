"""ファイルロック（15分おき巡回の二重起動防止）。"""
from __future__ import annotations

import os
import time
from pathlib import Path


class LockError(RuntimeError):
    pass


class FileLock:
    """O_EXCLで排他作成。stale_seconds超の古いlockはstaleとして解除する。"""

    def __init__(self, path, stale_seconds: int = 3600):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self._acquired = False

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > self.stale_seconds

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self._is_stale():
            # 古いlockは解除（前回プロセスが異常終了した想定）
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockError(f"既にロック中: {self.path}")
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {int(time.time())}\n")
        self._acquired = True
        return self

    def release(self) -> None:
        if self._acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
