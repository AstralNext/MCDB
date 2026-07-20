#!/usr/bin/env python3
"""校验 source / review 对齐，统计校对与缺译。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_all_review_titles, parse_review_line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source_root = args.root / "source"
    review_root = args.root / "review" / "titles"
    errors = 0

    source_ids: set[str] = set()
    for path in sorted(source_root.rglob("*.jsonl")):
        for i, line in enumerate(path.open(encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"ERROR {path}:{i} JSON {e}")
                errors += 1
                continue
            pid = o.get("id")
            if not pid:
                print(f"ERROR {path}:{i} missing id")
                errors += 1
                continue
            source_ids.add(pid)

    review = load_all_review_titles(review_root)
    review_ids = set(review)
    status = Counter(r["status"] for r in review.values())
    empty_zh = sum(1 for r in review.values() if not (r.get("zh") or "").strip())

    only_review = review_ids - source_ids
    only_source = source_ids - review_ids
    for pid in sorted(only_review)[:20]:
        print(f"ERROR review id not in source: {pid}")
        errors += 1
    if len(only_review) > 20:
        print(f"ERROR ... and {len(only_review) - 20} more orphan review ids")
        errors += 1

    # 格式抽查
    for path in sorted(review_root.rglob("*.jsonl")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            if parse_review_line(line) is None:
                print(f"ERROR bad review line {path}:{i}")
                errors += 1

    print("--- summary ---")
    print(f"source_ids={len(source_ids)} review_ids={len(review_ids)}")
    print(f"only_source(missing review)={len(only_source)}")
    print(f"status={json.dumps(dict(status), ensure_ascii=True)}")
    print(f"empty_zh={empty_zh}")
    print(f"errors={errors}")
    # 缺 review 行算警告但不阻断（crawl 后可能短暂不一致）；orphan review 算错
    if only_source:
        print(f"WARN {len(only_source)} source ids lack review lines")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
