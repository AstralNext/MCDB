#!/usr/bin/env python3
"""每日全量爬取 Modrinth（纯 JSONL，无数据库）。

- 读现有 source/ 作为已有目录
- 全量扫 API，合并新项目 / 刷新元数据
- 重写 source/ 分片
- 仅为新增 id 追加 review/titles 待译行
- 进度写入 state/crawl_progress.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    CRAWL_PROGRESS,
    REVIEW_TITLES,
    SOURCE_DIR,
    ensure_dirs,
    format_review_line,
    load_all_review_titles,
    now_iso,
    parse_review_line,
    safe_type,
    write_json,
)

API = "https://api.modrinth.com/v2/search"
USER_AGENT = "mcdb-collab/1.0 (github.com/mcdb; crawl)"
PAGE_SIZE = 100
DEFAULT_DELAY = 0.35
SHARD_SIZE = 3000


def request_page(offset: int, limit: int, timeout: float = 60.0) -> dict:
    params = {
        "limit": str(limit),
        "offset": str(offset),
        "index": "downloads",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                remaining = resp.headers.get("X-Ratelimit-Remaining")
                reset = resp.headers.get("X-Ratelimit-Reset")
                data = json.load(resp)
                if remaining is not None and int(remaining) <= 5:
                    wait = int(reset or 5) + 1
                    print(f"  rate-limit low ({remaining}), sleep {wait}s", flush=True)
                    time.sleep(wait)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("X-Ratelimit-Reset") or 10) + 1
                print(f"  429, sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            if e.code >= 500:
                time.sleep(min(60, 2**attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(min(60, 2**attempt))
    raise RuntimeError(f"failed after retries at offset={offset}")


def pick_fields(hit: dict) -> dict | None:
    pid = hit.get("project_id")
    title = hit.get("title")
    slug = hit.get("slug")
    if not pid or not title or not slug:
        return None
    return {
        "id": pid,
        "slug": slug,
        "type": hit.get("project_type") or "unknown",
        "title": title,
        "description": hit.get("description") or "",
        "downloads": int(hit.get("downloads") or 0),
        "updated": hit.get("date_modified"),
    }


def load_source_catalog() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not SOURCE_DIR.is_dir():
        return out
    for path in SOURCE_DIR.rglob("*.jsonl"):
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            out[o["id"]] = o
    return out


def write_source_shards(catalog: dict[str, dict], shard_size: int) -> int:
    import shutil

    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in catalog.values():
        by_type[safe_type(row.get("type"))].append(row)

    if SOURCE_DIR.exists():
        shutil.rmtree(SOURCE_DIR)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    for ptype, items in by_type.items():
        items.sort(key=lambda r: (-int(r.get("downloads") or 0), r["id"]))
        d = SOURCE_DIR / ptype
        d.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(items), shard_size):
            chunk = items[i : i + shard_size]
            path = d / f"{i // shard_size:03d}.jsonl"
            with path.open("w", encoding="utf-8", newline="\n") as f:
                for r in chunk:
                    obj = {
                        "id": r["id"],
                        "slug": r.get("slug") or "",
                        "type": r.get("type") or ptype,
                        "title": r.get("title") or "",
                        "description": r.get("description") or "",
                        "downloads": int(r.get("downloads") or 0),
                        "updated": r.get("updated"),
                    }
                    f.write(
                        json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            total += len(chunk)
    return total


def append_review_pending(new_rows: list[dict], shard_size: int) -> int:
    if not new_rows:
        return 0
    existing = set(load_all_review_titles(REVIEW_TITLES))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in new_rows:
        if r["id"] in existing:
            continue
        by_type[safe_type(r.get("type"))].append(r)

    added = 0
    for ptype, items in by_type.items():
        items.sort(key=lambda r: (-int(r.get("downloads") or 0), r["id"]))
        d = REVIEW_TITLES / ptype
        d.mkdir(parents=True, exist_ok=True)
        files = sorted(d.glob("*.jsonl"))
        if files:
            last = files[-1]
            count = sum(
                1
                for ln in last.read_text(encoding="utf-8").splitlines()
                if parse_review_line(ln)
            )
            shard_idx = int(last.stem)
            buf_path = last
            buf_count = count
        else:
            shard_idx = 0
            buf_path = d / "000.jsonl"
            buf_count = 0
            buf_path.write_text("", encoding="utf-8")

        for r in items:
            if buf_count >= shard_size:
                shard_idx += 1
                buf_path = d / f"{shard_idx:03d}.jsonl"
                buf_path.write_text("", encoding="utf-8")
                buf_count = 0
            line = format_review_line(r["id"], r.get("title") or "", "", "pending")
            with buf_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            buf_count += 1
            added += 1
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Modrinth crawl (JSONL only)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--shard-size", type=int, default=SHARD_SIZE)
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    catalog = load_source_catalog()
    before = len(catalog)
    print(f"loaded source catalog={before}", flush=True)

    offset = 0
    total_hits = None
    pages = 0
    new_rows: list[dict] = []
    inserted = 0
    refreshed = 0

    while True:
        if args.max_pages and pages >= args.max_pages:
            break
        data = request_page(offset, PAGE_SIZE)
        pages += 1
        total_hits = data.get("total_hits", total_hits)
        hits = data.get("hits") or []
        if not hits:
            break
        for hit in hits:
            row = pick_fields(hit)
            if not row:
                continue
            pid = row["id"]
            if pid not in catalog:
                catalog[pid] = row
                new_rows.append(row)
                inserted += 1
            else:
                catalog[pid] = row
                refreshed += 1
        offset += len(hits)
        print(
            f"  offset={offset}/{total_hits} new={inserted} refreshed={refreshed}",
            flush=True,
        )
        if total_hits is not None and offset >= int(total_hits):
            break
        time.sleep(args.delay)

    review_added = append_review_pending(new_rows, args.shard_size)
    exported = 0
    if not args.skip_export:
        exported = write_source_shards(catalog, args.shard_size)

    progress = {
        "updated_at": now_iso(),
        "total_hits_reported": total_hits,
        "unique_projects": len(catalog),
        "before": before,
        "new_inserted": inserted,
        "meta_refreshed": refreshed,
        "review_lines_appended": review_added,
        "source_exported": exported,
        "pages": pages,
        "storage": "jsonl",
    }
    write_json(CRAWL_PROGRESS, progress)
    print(json.dumps(progress, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
