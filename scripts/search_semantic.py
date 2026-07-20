#!/usr/bin/env python3
"""语义检索 CLI：读 dist/semantic/*.jsonl（全 JSON，无 sqlite）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compile_dist import cosine, decode_vec, embed
from common import DIST_DIR


def iter_semantic(root: Path):
    if root.is_file():
        paths = [root]
    else:
        paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


def search(semantic_path: Path, query: str, top_k: int = 10) -> list[tuple[float, dict]]:
    q = embed(query)
    scored: list[tuple[float, dict]] = []
    for r in iter_semantic(semantic_path):
        vec = decode_vec(r["v"])
        score = cosine(q, vec)
        scored.append(
            (
                score,
                {
                    "id": r.get("id"),
                    "slug": r.get("slug"),
                    "type": r.get("type"),
                    "en": r.get("en"),
                    "zh": r.get("zh"),
                    "downloads": r.get("downloads"),
                },
            )
        )
    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic search zh → en (JSON)")
    parser.add_argument("query", help="中文查询")
    parser.add_argument(
        "--semantic",
        type=Path,
        default=DIST_DIR / "semantic",
        help="semantic 目录或单个 .jsonl",
    )
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args()
    if not args.semantic.exists():
        print(
            f"缺少 {args.semantic}，先跑 python scripts/compile_dist.py",
            file=sys.stderr,
        )
        return 2
    for score, row in search(args.semantic, args.query, args.k):
        print(f"{score:.4f}\t{row['en']}\t{row['zh']}\t{row['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
