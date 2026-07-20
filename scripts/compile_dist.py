#!/usr/bin/env python3
"""编译对外产物：精确匹配表 + 语义向量库 + 版本信息。

用途：
- exact：翻译时按英文原名精确替换中文
- semantic：输入中文，找意思接近的英文名（搜索/联想）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    DIST_DIR,
    REVIEW_TITLES,
    SOURCE_DIR,
    ensure_dirs,
    load_all_review_titles,
    now_iso,
    write_json,
)

VEC_DIM = 256
NGRAM_N = 2


def char_ngrams(text: str, n: int = NGRAM_N) -> list[str]:
    t = re.sub(r"\s+", "", (text or "").lower())
    if not t:
        return []
    if len(t) < n:
        return [t]
    return [t[i : i + n] for i in range(len(t) - n + 1)]


def embed(text: str, dim: int = VEC_DIM) -> list[float]:
    """字符 uni/bi-gram 哈希向量（无需模型，中英混排短标题够用）。"""
    vec = [0.0] * dim
    grams = char_ngrams(text, 1) + char_ngrams(text, 2)
    if not grams:
        return vec
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        # 单字权重略高，利于短中文查询
        w = 1.4 if len(g) == 1 else 1.0
        vec[idx] += sign * w
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def load_pairs() -> list[dict]:
    """优先 review（人工/机翻），英文以 review.en 为准；补 slug/type 从 source。"""
    review = load_all_review_titles(REVIEW_TITLES)
    source_meta: dict[str, dict] = {}
    if SOURCE_DIR.is_dir():
        for path in SOURCE_DIR.rglob("*.jsonl"):
            for line in path.open(encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                source_meta[o["id"]] = o

    pairs = []
    for pid, row in review.items():
        zh = (row.get("zh") or "").strip()
        en = (row.get("en") or "").strip()
        if not en or not zh:
            continue
        if row.get("status") == "skip":
            continue
        meta = source_meta.get(pid, {})
        pairs.append(
            {
                "id": pid,
                "slug": meta.get("slug") or "",
                "type": row.get("type") or meta.get("type") or "",
                "en": en,
                "zh": zh,
                "status": row.get("status") or "machine",
                "downloads": int(meta.get("downloads") or 0),
            }
        )
    pairs.sort(key=lambda x: (-x["downloads"], x["id"]))
    return pairs


def build_exact(pairs: list[dict]) -> dict:
    by_id = {}
    by_en: dict[str, str] = {}
    by_en_lower: dict[str, str] = {}
    collisions = 0
    for p in pairs:
        by_id[p["id"]] = {"en": p["en"], "zh": p["zh"], "slug": p["slug"]}
        if p["en"] in by_en and by_en[p["en"]] != p["zh"]:
            collisions += 1
        # 下载量高的优先（已排序）
        by_en.setdefault(p["en"], p["zh"])
        by_en_lower.setdefault(p["en"].lower(), p["zh"])
    return {
        "by_id": by_id,
        "by_en": by_en,
        "by_en_lower": by_en_lower,
        "count": len(by_id),
        "en_collisions": collisions,
    }


def build_semantic_db(pairs: list[dict], out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    conn.executescript(
        """
        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE entries (
          id TEXT PRIMARY KEY,
          slug TEXT,
          type TEXT,
          en TEXT NOT NULL,
          zh TEXT NOT NULL,
          status TEXT,
          downloads INTEGER DEFAULT 0,
          vec BLOB NOT NULL
        );
        CREATE INDEX idx_entries_zh ON entries(zh);
        CREATE INDEX idx_entries_en ON entries(en);
        """
    )
    # 语义检索面向「中文查询 → 英文」：对中文标题建向量
    for p in pairs:
        vec = embed(p["zh"])
        conn.execute(
            """
            INSERT INTO entries (id, slug, type, en, zh, status, downloads, vec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["id"],
                p["slug"],
                p["type"],
                p["en"],
                p["zh"],
                p["status"],
                p["downloads"],
                pack_vec(vec),
            ),
        )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("vec_dim", str(VEC_DIM)),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("embed", f"char-{NGRAM_N}gram-hash"),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("built_at", now_iso()),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("count", str(len(pairs))),
    )
    conn.commit()
    conn.close()


def content_version(pairs: list[dict]) -> str:
    h = hashlib.sha256()
    for p in pairs:
        h.update(f"{p['id']}|{p['en']}|{p['zh']}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile exact + semantic dist")
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    ensure_dirs()
    args.out.mkdir(parents=True, exist_ok=True)

    pairs = load_pairs()
    exact = build_exact(pairs)
    exact_path = args.out / "exact_titles.json"
    write_json(exact_path, exact)

    # 中英对照：一行一条，方便直接读 / 导入
    bilingual_path = args.out / "bilingual.jsonl"
    with bilingual_path.open("w", encoding="utf-8", newline="\n") as f:
        for p in pairs:
            f.write(
                json.dumps(
                    {
                        "id": p["id"],
                        "slug": p.get("slug") or "",
                        "type": p.get("type") or "",
                        "en": p["en"],
                        "zh": p["zh"],
                        "status": p.get("status") or "",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    sem_path = args.out / "semantic.sqlite"
    build_semantic_db(pairs, sem_path)

    status_counts = Counter(p["status"] for p in pairs)
    version = {
        "version": content_version(pairs),
        "built_at": now_iso(),
        "pair_count": len(pairs),
        "status_counts": dict(status_counts),
        "files": {
            "bilingual": "bilingual.jsonl",
            "exact": "exact_titles.json",
            "semantic": "semantic.sqlite",
        },
        "exact_path": "exact_titles.json",
        "semantic_path": "semantic.sqlite",
        "bilingual_path": "bilingual.jsonl",
        "embed": f"char-unigram+bigram-hash/{VEC_DIM}",
        "usage": {
            "bilingual": "bilingual.jsonl：中英对照（id/slug/en/zh）",
            "translate_replace": "exact_titles.json by_en / by_id：英文原名 → 中文",
            "semantic_search": "semantic.sqlite：中文查询向量 → 近邻英文名",
        },
    }
    write_json(args.out / "version.json", version)
    print(json.dumps(version, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
