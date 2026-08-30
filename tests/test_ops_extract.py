"""ops.extract.extract_staff_booking() の許可リスト抽出テスト（会計/売上とは無関係）。

会計側BookingRecordやnormalize_booking()は一切importしない。実際のBeds24 raw dictの
形に近い、架空の名前・IDを使った合成フィクスチャのみを使う。
"""
import dataclasses

from yuge_finance.ops.extract import (classify_room_type_key, extract_staff_booking,
                                      load_room_type_config)
from yuge_finance.ops.schema import assert_no_financial_keys

TEST_ROOM_TYPES = {
    "single": {"label": "シングル", "capacity_rooms": 2, "match": {"room_ids": ["100"]}},
    "twin": {"label": "ツイン", "capacity_rooms": 3, "match": {"room_ids": ["200"]}},
    "unknown": {"label": "未分類", "capacity_rooms": 0, "match": {"room_ids": []}},
}


def _raw(**overrides):
    base = {
        "id": "9001", "roomId": "100", "firstName": "太郎", "lastName": "架空",
        "refererEditable": "じゃらんnet", "arrival": "2026-09-01", "departure": "2026-09-03",
        "numAdult": 2, "numChild": 1, "status": "confirmed",
        "phone": "090-0000-0000", "notes": "禁煙希望",
        "postcode": "990-2301", "state": "山形県", "city": "山形市",
        "address": "蔵王温泉123-4",
    }
    base.update(overrides)
    return base


# ---------------- 実configの読み込み ----------------
def test_load_room_type_config_returns_real_yml_keys():
    cfg = load_room_type_config()
    assert "single_toilet" in cfg
    assert cfg["single_toilet"]["label"]


# ---------------- 全体マッピング ----------------
def test_extract_full_allowlist_mapping():
    rec = extract_staff_booking(_raw(), TEST_ROOM_TYPES)
    assert rec.booking_id == "9001"
    assert rec.guest_name == "太郎 架空"
    assert rec.ota_name == "じゃらん"
    assert rec.booking_source_raw == "じゃらんnet"
    assert rec.room_type_key == "single"
    assert rec.room_type_label == "シングル"
    assert rec.checkin_date == "2026-09-01"
    assert rec.checkout_date == "2026-09-03"
    assert rec.status == "confirmed"
    assert rec.phone == "090-0000-0000"
    assert rec.notes == "禁煙希望"


# ---------------- adults/children/total_guests ----------------
def test_adults_and_children_are_split_not_merged():
    rec = extract_staff_booking(_raw(numAdult=2, numChild=3), TEST_ROOM_TYPES)
    assert rec.adults == 2
    assert rec.children == 3
    assert rec.total_guests == 5


def test_guest_counts_default_to_zero_when_missing():
    raw = _raw()
    del raw["numAdult"]
    del raw["numChild"]
    rec = extract_staff_booking(raw, TEST_ROOM_TYPES)
    assert rec.adults == 0
    assert rec.children == 0
    assert rec.total_guests == 0


def test_guest_counts_coerce_string_and_float_like_normalize_booking():
    rec = extract_staff_booking(_raw(numAdult="2", numChild="0"), TEST_ROOM_TYPES)
    assert rec.adults == 2
    assert rec.children == 0


# ---------------- OTA正規化(normalize_booking_sourceを実際に呼んでいることの確認) ----------------
def test_ota_normalization_maps_known_raw_value():
    rec = extract_staff_booking(_raw(refererEditable="じゃらんnet"), TEST_ROOM_TYPES)
    assert rec.ota_name == "じゃらん"
    assert rec.booking_source_raw == "じゃらんnet"


def test_ota_normalization_passes_through_unknown_raw_value_unchanged():
    rec = extract_staff_booking(_raw(refererEditable="未知OTA"), TEST_ROOM_TYPES)
    # 「不明」や「その他」に丸めず、生値をそのまま表示名として使う。
    assert rec.ota_name == "未知OTA"
    assert rec.booking_source_raw == "未知OTA"


# ---------------- 部屋タイプ解決 ----------------
def test_room_type_resolves_via_config():
    assert classify_room_type_key("200", TEST_ROOM_TYPES) == "twin"
    assert classify_room_type_key("999999", TEST_ROOM_TYPES) == "unknown"


def test_unresolvable_room_id_classified_as_unknown():
    rec = extract_staff_booking(_raw(roomId="999999"), TEST_ROOM_TYPES)
    assert rec.room_type_key == "unknown"
    assert rec.room_type_label == "未分類"


# ---------------- room_number（物理部屋番号は現状データに存在しない） ----------------
def test_room_number_stays_none_when_no_unit_field_present():
    rec = extract_staff_booking(_raw(), TEST_ROOM_TYPES)
    assert rec.room_number is None


def test_room_number_is_none_when_unit_mapping_is_empty_even_if_raw_unit_id_present():
    """2026-08-30判明: Beds24のunitIdは客室タイプ内の1始まり連番であり、実物理客室番号
    そのものではない。config/kiraku_room_unit_mapping.ymlのマッピングが無い(空)場合は、
    unitIdが存在してもroom_numberはNone(推測で実物理客室番号を作らない)。"""
    rec = extract_staff_booking(_raw(unitId="7"), TEST_ROOM_TYPES, {})
    assert rec.room_number is None


def test_room_number_resolves_via_room_unit_mapping_when_configured():
    """room_unit_mappingに(room_type_key, unitId) -> 実物理客室番号の対応があれば解決する。"""
    mapping = {"single": {"7": "401"}}
    rec = extract_staff_booking(_raw(unitId="7"), TEST_ROOM_TYPES, mapping)
    assert rec.room_number == "401"


def test_room_number_stays_none_for_an_unmapped_unit_id():
    """マッピングに対象のunitIdが無ければNone(他の客室タイプ/unitIdだけ埋まっていても
    誤って流用しない)。"""
    mapping = {"single": {"1": "401"}}  # unitId "7" は登録されていない
    rec = extract_staff_booking(_raw(unitId="7"), TEST_ROOM_TYPES, mapping)
    assert rec.room_number is None


# ---------------- arrival_time ----------------
def test_arrival_time_is_none_when_absent():
    rec = extract_staff_booking(_raw(), TEST_ROOM_TYPES)
    assert rec.arrival_time is None


def test_arrival_time_found_defensively_if_field_ever_appears():
    """現状の実データには存在しないが、arriv+time/eta/hourを含むキーが将来出現した
    場合に自動的に拾えることを確認する(架空フィールド)。"""
    rec = extract_staff_booking(_raw(estimatedArrivalTime="14:30"), TEST_ROOM_TYPES)
    assert rec.arrival_time == "14:30"


# ---------------- phone/notes/address サニタイズ ----------------
def test_phone_prefers_phone_then_mobile_then_none():
    assert extract_staff_booking(_raw(phone="03-1111-2222", mobile="090-9999-9999"),
                                 TEST_ROOM_TYPES).phone == "03-1111-2222"
    raw = _raw()
    del raw["phone"]
    raw["mobile"] = "090-9999-9999"
    assert extract_staff_booking(raw, TEST_ROOM_TYPES).phone == "090-9999-9999"


def test_notes_fallback_order_notes_comments_groupnote_message():
    raw = _raw()
    del raw["notes"]
    raw["comments"] = "コメント欄"
    assert extract_staff_booking(raw, TEST_ROOM_TYPES).notes == "コメント欄"

    raw2 = _raw()
    del raw2["notes"]
    raw2["groupNote"] = "グループ備考"
    assert extract_staff_booking(raw2, TEST_ROOM_TYPES).notes == "グループ備考"

    raw3 = _raw()
    del raw3["notes"]
    raw3["message"] = "メッセージ"
    assert extract_staff_booking(raw3, TEST_ROOM_TYPES).notes == "メッセージ"


def test_sanitization_treats_null_like_literals_as_none():
    for literal in ("None", "null", "undefined", "N/A", "none", "", "   "):
        rec = extract_staff_booking(_raw(phone=literal, notes=literal), TEST_ROOM_TYPES)
        assert rec.phone is None, f"phone={literal!r} should sanitize to None"
        assert rec.notes is None, f"notes={literal!r} should sanitize to None"


def test_sanitization_does_not_blank_normal_populated_values():
    rec = extract_staff_booking(_raw(phone="090-1234-5678", notes="ペット同伴あり"),
                                TEST_ROOM_TYPES)
    assert rec.phone == "090-1234-5678"
    assert rec.notes == "ペット同伴あり"


def test_address_full_case_maps_postcode_prefecture_city_rest():
    rec = extract_staff_booking(_raw(postcode="990-2301", state="山形県", city="山形市",
                                     address="蔵王温泉123-4"), TEST_ROOM_TYPES)
    assert rec.address.postcode == "990-2301"
    assert rec.address.prefecture == "山形県"
    assert rec.address.city == "山形市"
    assert rec.address.rest == "蔵王温泉123-4"


def test_address_partial_case_stays_partial_not_backfilled():
    raw = _raw()
    del raw["state"]
    del raw["city"]
    rec = extract_staff_booking(raw, TEST_ROOM_TYPES)
    assert rec.address.postcode == "990-2301"
    assert rec.address.prefecture is None
    assert rec.address.city is None
    assert rec.address.rest == "蔵王温泉123-4"


def test_address_all_null_like_fields_sanitize_to_none():
    rec = extract_staff_booking(
        _raw(postcode="None", state="null", city="undefined", address="   "),
        TEST_ROOM_TYPES)
    assert rec.address.postcode is None
    assert rec.address.prefecture is None
    assert rec.address.city is None
    assert rec.address.rest is None


# ---------------- 財務キー混入防止（回帰ガード） ----------------
def test_extracted_record_never_contains_financial_keys():
    rec = extract_staff_booking(_raw(), TEST_ROOM_TYPES)
    d = dataclasses.asdict(rec)
    assert_no_financial_keys(d)  # 例外が出なければOK
