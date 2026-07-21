#!/usr/bin/env python3
"""从 SQLite 导入为 source/ + locales/zh/ 分片 JSONL。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

DEFAULT_DB = Path(r"G:\ai爬虫\data\projects.db")
ROOT = Path(__file__).resolve().parents[1]
SHARD_SIZE = 3000
SAFE_TYPE = re.compile(r"[^a-zA-Z0-9_\-]+")


def safe_type(name: str | None) -> str:
    raw = (name or "unknown").strip() or "unknown"
    cleaned = SAFE_TYPE.sub("_", raw)
    return cleaned or "unknown"


def write_shards(
    base: Path, rows: list[dict], fields: list[str], shard_size: int
) -> int:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    files = 0
    for i in range(0, len(rows), shard_size):
        chunk = rows[i : i + shard_size]
        path = base / f"{files:03d}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as f:
            for row in chunk:
                obj = {k: row.get(k) for k in fields}
                f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        files += 1
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Modrinth translations SQLite → JSONL")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--shard-size", type=int, default=SHARD_SIZE)
    args = parser.parse_args()

    shard_size = max(100, int(args.shard_size))

    if not args.db.exists():
        raise SystemExit(f"DB 不存在: {args.db}")

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT project_id, slug, project_type, title, description,
               title_zh, description_zh, title_status, description_status,
               downloads, date_modified
        FROM projects
        ORDER BY downloads DESC, project_id
        """
    ).fetchall()
    conn.close()

    by_type: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_type[safe_type(r["project_type"])].append(r)

    source_root = args.root / "source"
    locale_root = args.root / "locales" / "zh"
    if source_root.exists():
        shutil.rmtree(source_root)
    if locale_root.exists():
        shutil.rmtree(locale_root)

    total_source = 0
    total_locale = 0
    summary = {}

    for ptype, items in sorted(by_type.items(), key=lambda x: (-len(x[1]), x[0])):
        source_rows = []
        locale_rows = []
        for r in items:
            source_rows.append(
                {
                    "id": r["project_id"],
                    "slug": r["slug"],
                    "type": r["project_type"] or ptype,
                    "title": r["title"] or "",
                    "description": r["description"] or "",
                    "downloads": int(r["downloads"] or 0),
                    "updated": r["date_modified"],
                }
            )
            locale_rows.append(
                {
                    "id": r["project_id"],
                    "title": r["title_zh"] or "",
                    "description": r["description_zh"] or "",
                    "title_status": r["title_status"] or "pending",
                    "desc_status": r["description_status"] or "pending",
                }
            )

        n_src = write_shards(
            source_root / ptype,
            source_rows,
            ["id", "slug", "type", "title", "description", "downloads", "updated"],
            shard_size,
        )
        n_loc = write_shards(
            locale_root / ptype,
            locale_rows,
            ["id", "title", "description", "title_status", "desc_status"],
            shard_size,
        )
        total_source += len(source_rows)
        total_locale += len(locale_rows)
        summary[ptype] = {
            "count": len(source_rows),
            "source_shards": n_src,
            "locale_shards": n_loc,
        }
        print(f"{ptype}: {len(source_rows)} rows → {n_src} shards")

    meta = {
        "total": total_source,
        "locale_total": total_locale,
        "shard_size": shard_size,
        "source_db": str(args.db),
        "by_type": summary,
    }
    (args.root / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"done. total={total_source} → {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
