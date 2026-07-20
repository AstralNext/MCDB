#!/usr/bin/env python3
"""语义检索 CLI：输入中文，返回意思接近的英文名。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compile_dist import cosine, embed, unpack_vec
from common import DIST_DIR


def search(db_path: Path, query: str, top_k: int = 10) -> list[tuple[float, dict]]:
    q = embed(query)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, slug, type, en, zh, status, downloads, vec FROM entries"
    ).fetchall()
    conn.close()
    scored = []
    for r in rows:
        score = cosine(q, unpack_vec(r["vec"]))
        scored.append(
            (
                score,
                {
                    "id": r["id"],
                    "slug": r["slug"],
                    "type": r["type"],
                    "en": r["en"],
                    "zh": r["zh"],
                    "downloads": r["downloads"],
                },
            )
        )
    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic search zh → en")
    parser.add_argument("query", help="中文查询")
    parser.add_argument("--db", type=Path, default=DIST_DIR / "semantic.sqlite")
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args()
    if not args.db.exists():
        print(f"缺少 {args.db}，先跑 python scripts/compile_dist.py", file=sys.stderr)
        return 2
    for score, row in search(args.db, args.query, args.k):
        print(f"{score:.4f}\t{row['en']}\t{row['zh']}\t{row['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
