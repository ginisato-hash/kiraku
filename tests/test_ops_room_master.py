"""KIRAKU_ROOM_ORDER(物理客室マスター)のテスト。18室固定・順序・除外客室の確認。"""
from yuge_finance.ops.room_master import KIRAKU_ROOM_ORDER, KIRAKU_ROOM_ORDER_SET


def test_kiraku_room_order_has_exactly_18_rooms():
    assert len(KIRAKU_ROOM_ORDER) == 18


def test_kiraku_room_order_exact_match():
    assert KIRAKU_ROOM_ORDER == [
        "401", "402", "403", "404", "405", "406",
        "501", "502", "503", "504", "505", "507",
        "601", "602", "603", "604", "605", "607",
    ]


def test_kiraku_room_order_excludes_obsolete_or_nonexistent_rooms():
    for obsolete in ["301", "302", "303", "304", "305", "306", "307", "407", "506", "606"]:
        assert obsolete not in KIRAKU_ROOM_ORDER_SET, f"{obsolete} must not be in the canonical room master"


def test_kiraku_room_order_has_no_duplicates():
    assert len(KIRAKU_ROOM_ORDER) == len(set(KIRAKU_ROOM_ORDER))


# ---------------- config/kiraku_room_unit_mapping.yml (real Beds24-confirmed values) ----------------
def test_real_room_unit_mapping_config_resolves_only_to_canonical_18_rooms():
    """2026-08-30、Beds24 API `/properties?includeAllRooms=true` を実際に照会して
    確認した(roomTypes[].units[].id -> units[].name)対応表。推測値ではない。
    このテストは、設定ファイルが将来誤って編集されて18室以外の値
    (旧客室301等)を返すようになっていないかの回帰防止。
    """
    from yuge_finance.ops.extract import load_room_unit_mapping

    mapping = load_room_unit_mapping()
    assert set(mapping.keys()) == {"single_toilet", "twin_toilet", "twin_bath", "family_washitsu"}

    all_room_numbers = []
    for room_type_key, unit_map in mapping.items():
        for unit_id, room_number in unit_map.items():
            assert room_number in KIRAKU_ROOM_ORDER_SET, (
                f"{room_type_key} unit {unit_id} resolves to {room_number!r}, "
                f"which is not in the canonical 18-room master")
            all_room_numbers.append(room_number)

    # 18室すべてが重複なくちょうど1回ずつ登場する(Beds24実データで確認済みの割当)。
    assert sorted(all_room_numbers) == sorted(KIRAKU_ROOM_ORDER)


def test_real_room_unit_mapping_config_matches_beds24_confirmed_values():
    """2026-08-30 Beds24 API実データそのままの値(単体テストとして固定)。"""
    from yuge_finance.ops.extract import load_room_unit_mapping

    mapping = load_room_unit_mapping()
    assert mapping["single_toilet"] == {"1": "607", "2": "507"}
    assert mapping["twin_bath"] == {"1": "501", "2": "502", "3": "401", "4": "402"}
    assert mapping["family_washitsu"] == {"1": "605", "2": "505"}
    assert mapping["twin_toilet"] == {
        "1": "601", "2": "602", "3": "603", "4": "604", "5": "503",
        "6": "504", "7": "403", "8": "404", "9": "405", "10": "406",
    }
