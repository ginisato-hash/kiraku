"""勘定科目解決・消費税計算（喜らく単体）。"""
from __future__ import annotations

from typing import Tuple

from .. import config


def _valid_account_names() -> set:
    return {a["name"] for a in config.accounts_cfg()["accounts"]}


def resolve(category: str) -> Tuple[str, str]:
    """論理カテゴリ名 -> (試算表科目, 補助科目)。

    1. category_map に定義があればそれを使う
    2. 既に正規の勘定科目名ならそのまま (補助=同名)
    3. いずれも該当しなければ仮勘定
    """
    cfg = config.accounts_cfg()
    cmap = cfg.get("category_map", {})
    if category in cmap:
        m = cmap[category]
        return m["account"], m.get("subaccount", category)
    if category in _valid_account_names():
        return category, category
    return cfg.get("suspense_account", "その他流動資産"), \
        cfg.get("suspense_subaccount", "仮勘定_未分類")


def default_tax_rate() -> float:
    return float(config.kiraku().get("tax", {}).get("default_rate", 0.10))


def tax_category_label(rate: float = None) -> str:
    rate = default_tax_rate() if rate is None else rate
    return f"課税{int(round(rate * 100))}%"


def extract_tax_inclusive(amount: float, rate: float = None) -> float:
    """税込金額から消費税相当額を算出（四捨五入）。"""
    rate = default_tax_rate() if rate is None else rate
    return round(amount * rate / (1 + rate))
