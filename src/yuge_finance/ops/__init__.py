"""喜らく単体 スタッフ日次オペレーション（Daily Ops / 清掃指示）データレイヤー。

会計・売上（accounting/ 配下、BookingRecord等）とは完全に独立したパッケージ。
raw Beds24 JSON(data/raw/beds24/<month>/<month>.json)を直接読み、
売上を一切含まない運用データ(StaffBookingRecord/CleaningRoomState)のみを生成する。
"""
from __future__ import annotations
