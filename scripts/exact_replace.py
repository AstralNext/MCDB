#!/usr/bin/env python3
"""精确替换：英文原名 → 中文（读 dist/exact_titles.json）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DIST_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Exact EN→ZH title replace")
    parser.add_argument("text", nargs="?", help="英文名；省略则 stdin 逐行")
    parser.add_argument("--map", type=Path, default=DIST_DIR / "exact_titles.json")
    args = parser.parse_args()
    if not args.map.exists():
        print(f"缺少 {args.map}，先跑 compile_dist.py", file=sys.stderr)
        return 2
    data = json.loads(args.map.read_text(encoding="utf-8"))
    by_en = data.get("by_en") or {}
    by_lower = data.get("by_en_lower") or {}

    def repl(s: str) -> str:
        if s in by_en:
            return by_en[s]
        return by_lower.get(s.lower(), s)

    if args.text is not None:
        print(repl(args.text))
        return 0
    for line in sys.stdin:
        print(repl(line.rstrip("\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
