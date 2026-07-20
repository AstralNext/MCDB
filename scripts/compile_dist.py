#!/usr/bin/env python3
"""编译对外产物（全 JSON）：中英对照 + 精确表 + 语义向量 JSONL。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import shutil
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
SEMANTIC_SHARD = 3000


def char_ngrams(text: str, n: int = NGRAM_N) -> list[str]:
    t = re.sub(r"\s+", "", (text or "").lower())
    if not t:
        return []
    if len(t) < n:
        return [t]
    return [t[i : i + n] for i in range(len(t) - n + 1)]


def embed(text: str, dim: int = VEC_DIM) -> list[float]:
    vec = [0.0] * dim
    grams = char_ngrams(text, 1) + char_ngrams(text, 2)
    if not grams:
        return vec
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        w = 1.4 if len(g) == 1 else 1.0
        vec[idx] += sign * w
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def encode_vec(vec: list[float]) -> str:
    return base64.b64encode(pack_vec(vec)).decode("ascii")


def decode_vec(s: str) -> list[float]:
    return unpack_vec(base64.b64decode(s))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def load_pairs() -> list[dict]:
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
        desc_en = (row.get("desc") or meta.get("description") or "").strip()
        desc_zh = (row.get("desc_zh") or "").strip()
        pairs.append(
            {
                "id": pid,
                "slug": meta.get("slug") or "",
                "type": row.get("type") or meta.get("type") or "",
                "en": en,
                "zh": zh,
                "desc": desc_en,
                "desc_zh": desc_zh,
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
        by_en.setdefault(p["en"], p["zh"])
        by_en_lower.setdefault(p["en"].lower(), p["zh"])
    return {
        "by_id": by_id,
        "by_en": by_en,
        "by_en_lower": by_en_lower,
        "count": len(by_id),
        "en_collisions": collisions,
    }


def build_semantic_jsonl(pairs: list[dict], out_dir: Path, shard_size: int) -> list[str]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for i in range(0, len(pairs), shard_size):
        chunk = pairs[i : i + shard_size]
        name = f"{i // shard_size:03d}.jsonl"
        path = out_dir / name
        with path.open("w", encoding="utf-8", newline="\n") as f:
            for p in chunk:
                obj = {
                    "id": p["id"],
                    "slug": p.get("slug") or "",
                    "type": p.get("type") or "",
                    "en": p["en"],
                    "zh": p["zh"],
                    "status": p.get("status") or "",
                    "downloads": int(p.get("downloads") or 0),
                    "v": encode_vec(embed(p["zh"])),
                }
                f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        files.append(f"semantic/{name}")
    return files


def content_version(pairs: list[dict]) -> str:
    h = hashlib.sha256()
    for p in pairs:
        h.update(f"{p['id']}|{p['en']}|{p['zh']}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile JSON dist (no sqlite)")
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    parser.add_argument("--shard-size", type=int, default=SEMANTIC_SHARD)
    args = parser.parse_args()

    ensure_dirs()
    args.out.mkdir(parents=True, exist_ok=True)

    # 清理旧 sqlite
    old_db = args.out / "semantic.sqlite"
    if old_db.exists():
        old_db.unlink()

    pairs = load_pairs()
    write_json(args.out / "exact_titles.json", build_exact(pairs))

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
                        "desc": p.get("desc") or "",
                        "desc_zh": p.get("desc_zh") or "",
                        "status": p.get("status") or "",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    sem_files = build_semantic_jsonl(pairs, args.out / "semantic", args.shard_size)
    status_counts = Counter(p["status"] for p in pairs)
    version = {
        "version": content_version(pairs),
        "built_at": now_iso(),
        "pair_count": len(pairs),
        "status_counts": dict(status_counts),
        "format": "json-only",
        "files": {
            "bilingual": "bilingual.jsonl",
            "exact": "exact_titles.json",
            "semantic_dir": "semantic/",
            "semantic_shards": sem_files,
        },
        "embed": {
            "name": "char-unigram+bigram-hash",
            "dim": VEC_DIM,
            "vec_encoding": "base64-float32le",
        },
        "usage": {
            "bilingual": "bilingual.jsonl：中英对照",
            "translate_replace": "exact_titles.json：英文原名 → 中文",
            "semantic_search": "semantic/*.jsonl：中文向量近邻 → 英文",
        },
    }
    write_json(args.out / "version.json", version)
    write_json(
        args.out / "semantic_meta.json",
        {
            "dim": VEC_DIM,
            "count": len(pairs),
            "shards": sem_files,
            "vec_field": "v",
            "encoding": "base64-float32le",
        },
    )
    print(json.dumps(version, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
