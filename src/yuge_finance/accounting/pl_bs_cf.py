"""PL / BS / CF 集計（喜らく単体）。

試算表(科目別純額)から3表サマリを作る。Excelの数式表とは独立した
検算・CSV出力用サマリ。複式簿記が一致していればBSは恒等的に均衡する。
"""
from __future__ import annotations

from typing import Dict, List

# 科目 -> 区分
_ASSET = {"現預金", "売掛金", "その他流動資産", "有形固定資産"}
_LIAB = {"買掛金・未払金", "借入金", "その他負債"}
_EQUITY = {"資本金・資本剰余金", "利益剰余金"}
_REVENUE = {"宿泊売上", "その他売上"}
_EXPENSE = {"OTA手数料", "リネン費", "水道光熱費", "消耗品費", "修繕費", "人件費",
            "固定費", "MCコスト", "減価償却費"}
_NONOP = {"支払利息"}
_TAX = {"法人税等"}


def _net(tb: List[Dict], names: set, normal: str) -> float:
    """normal='debit' は (借-貸)、'credit' は (貸-借) を返す。"""
    total = 0.0
    for r in tb:
        if r["account"] in names:
            total += (r["net"] if normal == "debit" else -r["net"])
    return round(total, 2)


def build_pl(tb: List[Dict]) -> Dict:
    revenue = _net(tb, _REVENUE, "credit")
    expense = _net(tb, _EXPENSE, "debit")
    nonop = _net(tb, _NONOP, "debit")
    tax = _net(tb, _TAX, "debit")
    operating_profit = round(revenue - expense, 2)
    ordinary_profit = round(operating_profit - nonop, 2)
    net_income = round(ordinary_profit - tax, 2)
    lines = [
        {"item": "売上高", "amount": revenue},
        {"item": "営業費用合計", "amount": expense},
        {"item": "営業利益", "amount": operating_profit},
        {"item": "支払利息", "amount": nonop},
        {"item": "経常利益", "amount": ordinary_profit},
        {"item": "法人税等", "amount": tax},
        {"item": "当期純利益", "amount": net_income},
    ]
    return {"lines": lines, "net_income": net_income, "revenue": revenue}


def build_bs(tb: List[Dict], net_income: float) -> Dict:
    assets = _net(tb, _ASSET, "debit")
    liabilities = _net(tb, _LIAB, "credit")
    equity = _net(tb, _EQUITY, "credit")
    equity_with_ni = round(equity + net_income, 2)
    le_total = round(liabilities + equity_with_ni, 2)
    lines = [
        {"item": "資産合計", "amount": assets},
        {"item": "負債合計", "amount": liabilities},
        {"item": "純資産(資本)", "amount": equity},
        {"item": "当期純利益", "amount": net_income},
        {"item": "負債・純資産合計", "amount": le_total},
    ]
    return {"lines": lines, "assets": assets, "liabilities_equity": le_total,
            "balanced": abs(assets - le_total) <= 0.5}


def build_cf(tb: List[Dict], net_income: float) -> Dict:
    """簡易CF。現預金純増減を区分し、BS現預金増減と一致させる。"""
    cash_change = _net(tb, {"現預金"}, "debit")
    financing = _net(tb, {"借入金"}, "credit")          # 借入の純増=財務CF
    investing = -_net(tb, {"有形固定資産"}, "debit")     # 固定資産取得=投資CFマイナス
    operating = round(cash_change - financing - investing, 2)
    lines = [
        {"item": "営業CF", "amount": operating},
        {"item": "投資CF", "amount": investing},
        {"item": "財務CF", "amount": financing},
        {"item": "現預金純増減", "amount": cash_change},
    ]
    return {"lines": lines, "cash_change": cash_change,
            "reconciles": abs((operating + investing + financing) - cash_change) <= 0.5}
