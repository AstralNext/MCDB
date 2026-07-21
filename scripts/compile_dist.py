#!/usr/bin/env python3
"""编译对外产物（全 JSON）：中英对照 + 精确表。不再编译向量。"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def content_version(pairs: list[dict]) -> str:
    h = hashlib.sha256()
    for p in pairs:
        h.update(f"{p['id']}|{p['en']}|{p['zh']}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile JSON dist (titles only, no vectors)")
    parser.add_argument("--out", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    ensure_dirs()
    args.out.mkdir(parents=True, exist_ok=True)

    # 清理旧产物
    for name in ("semantic.sqlite", "semantic_meta.json"):
        old = args.out / name
        if old.exists():
            old.unlink()
    sem_dir = args.out / "semantic"
    if sem_dir.is_dir():
        import shutil

        shutil.rmtree(sem_dir)

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
        },
        "usage": {
            "bilingual": "bilingual.jsonl：中英对照（标题搜索 / 译名）",
            "translate_replace": "exact_titles.json：英文原名 → 中文",
        },
    }
    write_json(args.out / "version.json", version)
    print(json.dumps(version, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
