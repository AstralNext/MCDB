#!/usr/bin/env python3
"""标题模糊检索 CLI：读 bilingual.jsonl（精确 > 前缀 > 包含）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DIST_DIR


def norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def score_row(q: str, en: str, zh: str, slug: str) -> float:
    nq = norm(q)
    if not nq:
        return 0.0
    n_en, n_zh = norm(en), norm(zh)
    n_slug = (slug or "").lower()
    ascii_q = bool(re.fullmatch(r"[a-z0-9_.:-]+", q.strip(), flags=re.I))
    best = 0.0
    if n_en == nq or n_zh == nq:
        best = max(best, 5.0)
    if n_slug == nq:
        best = max(best, 4.5)
    if ascii_q:
        tokens = [t for t in re.split(r"[-_.]+", n_slug) if t]
        if nq in tokens and n_slug != nq:
            best = max(best, 1.15)
        if n_en != nq and re.search(
            rf"(?:^|[^a-z0-9]){re.escape(nq)}(?:[^a-z0-9]|$)", en or "", flags=re.I
        ):
            best = max(best, 1.35)
        return best
    for title in (n_zh, n_en):
        if not title or title == nq:
            continue
        if title.startswith(nq):
            best = max(best, 1.6 + len(nq) / max(len(title), 1))
        elif nq in title:
            best = max(best, 0.85 * (len(nq) / max(len(title), 1)))
    if n_slug != nq and nq in n_slug:
        best = max(best, 0.5 * (len(nq) / max(len(n_slug), 1)))
    return best


def search(path: Path, query: str, limit: int) -> list[dict]:
    hits: list[tuple[float, dict]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            s = score_row(query, o.get("en") or "", o.get("zh") or "", o.get("slug") or "")
            if s <= 0:
                continue
            hits.append(
                (
                    s,
                    {
                        "id": o.get("id") or "",
                        "en": o.get("en") or "",
                        "zh": o.get("zh") or "",
                        "slug": o.get("slug") or None,
                        "type": o.get("type") or None,
                        "score": round(s, 6),
                    },
                )
            )
    hits.sort(key=lambda x: -x[0])
    return [h for _, h in hits[:limit]]


def main() -> int:
    parser = argparse.ArgumentParser(description="MCDB title fuzzy search")
    parser.add_argument("query")
    parser.add_argument("-k", "--limit", type=int, default=12)
    parser.add_argument(
        "--bilingual",
        type=Path,
        default=DIST_DIR / "bilingual.jsonl",
    )
    args = parser.parse_args()
    if not args.bilingual.is_file():
        print(f"缺少 {args.bilingual}", file=sys.stderr)
        return 2
    for h in search(args.bilingual, args.query, args.limit):
        print(json.dumps(h, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
