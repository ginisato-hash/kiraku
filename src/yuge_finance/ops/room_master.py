"""喜らく物理客室マスター(canonical source of truth)。

18室固定。classifier / print / mobile / override validation / tests
すべてがこのリストを共有すること — 別ファイルへ個別にハードコードしない
(JS側は cloudflare/staff-ops/src/roomMaster.js に同一リストを持つ。
 どちらかを変更したら必ずもう一方も同期させること)。

301〜306・407・506・606 は現在使用していない/存在しないため含めない。
507・601〜605・607 は実在する。
"""

KIRAKU_ROOM_ORDER = [
    "401", "402", "403", "404", "405", "406",
    "501", "502", "503", "504", "505", "507",
    "601", "602", "603", "604", "605", "607",
]

KIRAKU_ROOM_ORDER_SET = frozenset(KIRAKU_ROOM_ORDER)
