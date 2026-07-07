"""Beds24予約 → 売上仕訳（喜らく単体）。

計上基準: checkin_month（チェックイン月に全額計上）。
キャンセル予約は計上しない（分析用には保存済み）。

仕訳:
  A) 売上計上 : 借方 売掛金(gross) / 貸方 宿泊売上(gross)
  B) OTA手数料: 借方 OTA手数料(commission) / 貸方 売掛金(commission)
入金は銀行明細(売掛金消込)で計上される。
"""
from __future__ import annotations

from typing import List

from .. import config
from ..normalize.schema import BookingRecord, JournalEntry
from . import tax_rules


def build(bookings: List[BookingRecord], month: str) -> List[JournalEntry]:
    cfg = config.kiraku()
    exclude = cfg.get("revenue", {}).get("exclude_statuses", ["cancelled"])
    basis = cfg.get("revenue", {}).get("recognition_basis", "checkin_month")
    prop = config.property_name()
    entries: List[JournalEntry] = []

    for b in bookings:
        if b.is_cancelled(exclude):
            continue
        if basis == "checkin_month":
            jdate = b.checkin_date
        else:  # 将来: stay_proration。現状はcheckin_dateで代用。
            jdate = b.checkin_date
        if not jdate or jdate[:7] != month:
            continue
        if b.gross_revenue <= 0:
            continue

        tax_lbl = tax_rules.tax_category_label()
        # A) 売上計上
        entries.append(JournalEntry(
            journal_date=jdate, property=prop,
            description=f"宿泊売上 {b.channel} {b.guest_name}".strip(),
            debit_account="売掛金", debit_subaccount=b.channel or "売掛金",
            debit_amount=b.gross_revenue,
            credit_account="宿泊売上", credit_subaccount=b.channel or "宿泊売上",
            credit_amount=b.gross_revenue,
            tax_category=tax_lbl, counterparty=b.channel,
            source="beds24", source_id=b.booking_id,
            confidence="high", rule_id="rev_checkin",
            memo=f"booking_id={b.booking_id} room={b.room_name}",
        ).finalize())

        # B) OTA手数料
        if b.ota_commission and b.ota_commission > 0:
            entries.append(JournalEntry(
                journal_date=jdate, property=prop,
                description=f"OTA手数料 {b.channel}".strip(),
                debit_account="OTA手数料", debit_subaccount=b.channel or "OTA手数料",
                debit_amount=b.ota_commission,
                credit_account="売掛金", credit_subaccount=b.channel or "売掛金",
                credit_amount=b.ota_commission,
                tax_category=tax_lbl, counterparty=b.channel,
                source="beds24", source_id=b.booking_id,
                confidence="high", rule_id="rev_commission",
                memo=f"booking_id={b.booking_id}",
            ).finalize())
    return entries
