#!/usr/bin/env python3
"""BI公開ヘルパー。`yuge-finance publish-bi` と同等。

data/output/latest/bi/ → cloudflare/bi-web/public/data/ へ
JSON検証・atomic replace・manifest生成を行う。
launchd や手動実行から venv の python で呼ぶ:
    .venv/bin/python cloudflare/scripts/publish_bi.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from yuge_finance import publish  # noqa: E402


def main() -> int:
    try:
        res = publish.publish()
    except publish.PublishError as e:
        print(f"[publish_bi] ERROR: {e}", file=sys.stderr)
        return 1
    print(f"[publish_bi] published {res['published']} files -> {res['dst']}")
    print(f"[publish_bi] checksum {res['checksum'][:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
