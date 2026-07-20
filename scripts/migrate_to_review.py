#!/usr/bin/env python3
"""把现有 source/ + locales/zh/ 迁成 review/titles：id|英文|中文|status。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    REVIEW_TITLES,
    SOURCE_DIR,
    ensure_dirs,
    format_review_line,
    safe_type,
)

ROOT = Path(__file__).resolve().parents[1]
LOCALE_DIR = ROOT / "locales" / "zh"
SHARD_SIZE = 3000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-size", type=int, default=SHARD_SIZE)
    parser.add_argument("--remove-locales", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    if not SOURCE_DIR.is_dir():
        raise SystemExit("缺少 source/")

    locale_by_id: dict[str, dict] = {}
    if LOCALE_DIR.is_dir():
        for path in LOCALE_DIR.rglob("*.jsonl"):
            for line in path.open(encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                locale_by_id[o["id"]] = o

    by_type: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for path in SOURCE_DIR.rglob("*.jsonl"):
        ptype = safe_type(path.parent.name)
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            src = json.loads(line)
            loc = locale_by_id.get(src["id"], {})
            by_type[ptype].append((src, loc))

    if REVIEW_TITLES.exists():
        shutil.rmtree(REVIEW_TITLES)
    REVIEW_TITLES.mkdir(parents=True, exist_ok=True)

    total = 0
    for ptype, items in sorted(by_type.items(), key=lambda x: (-len(x[1]), x[0])):
        items.sort(key=lambda x: (-int(x[0].get("downloads") or 0), x[0]["id"]))
        d = REVIEW_TITLES / ptype
        d.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(items), args.shard_size):
            chunk = items[i : i + args.shard_size]
            path = d / f"{i // args.shard_size:03d}.jsonl"
            with path.open("w", encoding="utf-8", newline="\n") as f:
                for src, loc in chunk:
                    zh = (loc.get("title") or "").strip()
                    ts = loc.get("title_status") or ("machine" if zh else "pending")
                    if ts == "done":
                        ts = "machine"
                    f.write(
                        format_review_line(src["id"], src.get("title") or "", zh, ts)
                        + "\n"
                    )
                    total += 1
        print(f"{ptype}: {len(items)} lines")

    if args.remove_locales and LOCALE_DIR.exists():
        shutil.rmtree(ROOT / "locales")
        print("removed locales/")

    print(f"done. review lines={total} → {REVIEW_TITLES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
