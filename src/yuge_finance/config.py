"""設定・パス・環境変数のロード（喜らく単体）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

# プロジェクトルート = このファイルから src/yuge_finance/ の2つ上
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
TEMPLATE_DIR = ROOT / "templates"
IMPORTS_DIR = ROOT / "imports"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

TEMPLATE_NAME = "kiraku_integrated_3statement_actual_model_日本語版_v1.xlsx"
DB_PATH = DATA_DIR / "ledger.sqlite"


def _load_env() -> None:
    """.env を簡易ロード（python-dotenv非依存）。既存の環境変数は上書きしない。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def env(key: str, default: str = "") -> str:
    _load_env()
    return os.environ.get(key, default)


@lru_cache(maxsize=None)
def load_yaml(name: str) -> Dict[str, Any]:
    """config/<name> を読み込む。"""
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def kiraku() -> Dict[str, Any]:
    return load_yaml("kiraku.yml")


def accounts_cfg() -> Dict[str, Any]:
    return load_yaml("accounts.yml")


def property_name() -> str:
    return kiraku().get("property", {}).get("name", "喜らく")


def template_path() -> Path:
    return TEMPLATE_DIR / TEMPLATE_NAME


def output_dir(month: str) -> Path:
    d = DATA_DIR / "output" / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, TEMPLATE_DIR, IMPORTS_DIR, DATA_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
