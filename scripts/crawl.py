#!/usr/bin/env python3
"""每日全量爬取 Modrinth：只入库尚不存在的项目，写进度，同步 source/ 与 review 待译行。"""

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
    CATALOG_DB,
    CRAWL_PROGRESS,
    REVIEW_TITLES,
    SOURCE_DIR,
    connect_db,
    ensure_dirs,
    format_review_line,
    now_iso,
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
                wait = min(60, 2**attempt)
                time.sleep(wait)
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
        "project_id": pid,
        "slug": slug,
        "project_type": hit.get("project_type"),
        "title": title,
        "description": hit.get("description") or "",
        "downloads": int(hit.get("downloads") or 0),
        "date_modified": hit.get("date_modified"),
    }


def insert_new(conn, row: dict, fetched_at: str) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO projects (
          project_id, slug, project_type, title, description,
          downloads, date_modified, fetched_at,
          title_status, description_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending')
        """,
        (
            row["project_id"],
            row["slug"],
            row["project_type"],
            row["title"],
            row["description"],
            row["downloads"],
            row["date_modified"],
            fetched_at,
        ),
    )
    return cur.rowcount > 0


def update_meta_if_exists(conn, row: dict) -> None:
    """已存在则刷新 downloads / date_modified / 英文 title/description（不碰译文）。"""
    conn.execute(
        """
        UPDATE projects SET
          slug = ?,
          project_type = ?,
          title = ?,
          description = ?,
          downloads = ?,
          date_modified = ?
        WHERE project_id = ?
        """,
        (
            row["slug"],
            row["project_type"],
            row["title"],
            row["description"],
            row["downloads"],
            row["date_modified"],
            row["project_id"],
        ),
    )


def export_source_shards(conn, shard_size: int = SHARD_SIZE) -> int:
    rows = conn.execute(
        """
        SELECT project_id, slug, project_type, title, description,
               downloads, date_modified
        FROM projects
        ORDER BY downloads DESC, project_id
        """
    ).fetchall()
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[safe_type(r["project_type"])].append(r)

    if SOURCE_DIR.exists():
        import shutil

        shutil.rmtree(SOURCE_DIR)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    for ptype, items in by_type.items():
        d = SOURCE_DIR / ptype
        d.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(items), shard_size):
            chunk = items[i : i + shard_size]
            path = d / f"{i // shard_size:03d}.jsonl"
            with path.open("w", encoding="utf-8", newline="\n") as f:
                for r in chunk:
                    obj = {
                        "id": r["project_id"],
                        "slug": r["slug"],
                        "type": r["project_type"] or ptype,
                        "title": r["title"] or "",
                        "description": r["description"] or "",
                        "downloads": int(r["downloads"] or 0),
                        "updated": r["date_modified"],
                    }
                    f.write(
                        json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            total += len(chunk)
    return total


def append_review_pending(conn, new_ids: list[str], shard_size: int = SHARD_SIZE) -> int:
    """仅为新增 id 追加到对应类型末尾分片（不覆盖已有 review 行）。"""
    if not new_ids:
        return 0
    # 已有 id 集合（按转义后首字段）
    existing: set[str] = set()
    if REVIEW_TITLES.is_dir():
        from common import parse_review_line

        for path in REVIEW_TITLES.rglob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = parse_review_line(line)
                if row:
                    existing.add(row["id"])

    added = 0
    by_type: dict[str, list] = defaultdict(list)
    qmarks = ",".join("?" * len(new_ids))
    for r in conn.execute(
        f"""
        SELECT project_id, project_type, title, downloads
        FROM projects WHERE project_id IN ({qmarks})
        ORDER BY downloads DESC, project_id
        """,
        new_ids,
    ):
        if r["project_id"] in existing:
            continue
        by_type[safe_type(r["project_type"])].append(r)

    for ptype, items in by_type.items():
        d = REVIEW_TITLES / ptype
        d.mkdir(parents=True, exist_ok=True)
        existing_files = sorted(d.glob("*.jsonl"))
        if existing_files:
            last = existing_files[-1]
            lines = [
                ln
                for ln in last.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            shard_idx = int(last.stem)
            buf_path = last
            buf_count = len(lines)
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
            line = format_review_line(r["project_id"], r["title"] or "", "", "pending")
            with buf_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            buf_count += 1
            added += 1
    return added


def bootstrap_from_source(conn) -> int:
    """若库空且存在 source/，从 JSONL 灌入。"""
    n = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    if n > 0 or not SOURCE_DIR.is_dir():
        return 0
    inserted = 0
    fetched_at = now_iso()
    for path in SOURCE_DIR.rglob("*.jsonl"):
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            row = {
                "project_id": o["id"],
                "slug": o.get("slug") or o["id"],
                "project_type": o.get("type"),
                "title": o.get("title") or "",
                "description": o.get("description") or "",
                "downloads": int(o.get("downloads") or 0),
                "date_modified": o.get("updated"),
            }
            if insert_new(conn, row, fetched_at):
                inserted += 1
    conn.commit()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Modrinth crawl (new-only insert)")
    parser.add_argument("--db", type=Path, default=CATALOG_DB)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument(
        "--fresh-checkpoint",
        action="store_true",
        help="从 offset=0 重新扫一遍（仍只 INSERT OR IGNORE 新项，并刷新元数据）",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="调试：最多翻页数，0=不限")
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    conn = connect_db(args.db)
    boot = bootstrap_from_source(conn)
    if boot:
        print(f"bootstrapped {boot} rows from source/", flush=True)

    ck = conn.execute(
        "SELECT offset, total_hits, inserted, skipped_dup, done FROM crawl_checkpoint WHERE id=1"
    ).fetchone()
    offset = 0 if args.fresh_checkpoint else int(ck["offset"] or 0)
    # 每日全量：总是从 0 扫，用 INSERT OR IGNORE 拿新的
    offset = 0
    inserted = 0
    skipped = 0
    refreshed = 0
    new_ids: list[str] = []
    total_hits = None
    pages = 0

    print(f"crawl start offset=0 db={args.db}", flush=True)
    while True:
        if args.max_pages and pages >= args.max_pages:
            break
        data = request_page(offset, PAGE_SIZE)
        pages += 1
        total_hits = data.get("total_hits", total_hits)
        hits = data.get("hits") or []
        if not hits:
            break
        fetched_at = now_iso()
        for hit in hits:
            row = pick_fields(hit)
            if not row:
                continue
            if insert_new(conn, row, fetched_at):
                inserted += 1
                new_ids.append(row["project_id"])
            else:
                skipped += 1
                update_meta_if_exists(conn, row)
                refreshed += 1
        offset += len(hits)
        conn.execute(
            """
            UPDATE crawl_checkpoint SET
              offset=?, total_hits=?, inserted=?, skipped_dup=?, done=0, updated_at=?
            WHERE id=1
            """,
            (
                offset,
                total_hits,
                conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                skipped,
                now_iso(),
            ),
        )
        conn.commit()
        print(
            f"  offset={offset}/{total_hits} new={inserted} seen={skipped}",
            flush=True,
        )
        if total_hits is not None and offset >= int(total_hits):
            break
        time.sleep(args.delay)

    conn.execute(
        "UPDATE crawl_checkpoint SET done=1, updated_at=? WHERE id=1",
        (now_iso(),),
    )
    conn.commit()

    review_added = append_review_pending(conn, new_ids)
    exported = 0
    if not args.skip_export:
        exported = export_source_shards(conn)

    unique = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    pending_titles = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE title_status='pending'"
    ).fetchone()[0]

    progress = {
        "updated_at": now_iso(),
        "total_hits_reported": total_hits,
        "unique_projects": unique,
        "new_inserted": inserted,
        "already_seen": skipped,
        "meta_refreshed": refreshed,
        "review_lines_appended": review_added,
        "source_exported": exported,
        "pending_titles": pending_titles,
        "pages": pages,
    }
    write_json(CRAWL_PROGRESS, progress)
    print(json.dumps(progress, ensure_ascii=False, indent=2), flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
