#!/usr/bin/env python3
"""共享路径、SQLite schema、进度读写。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE_TYPE = re.compile(r"[^a-zA-Z0-9_\-]+")

CATALOG_DB = ROOT / "data" / "catalog.db"
SOURCE_DIR = ROOT / "source"
REVIEW_TITLES = ROOT / "review" / "titles"
STATE_DIR = ROOT / "state"
DIST_DIR = ROOT / "dist"

CRAWL_PROGRESS = STATE_DIR / "crawl_progress.json"
TRANSLATE_PROGRESS = STATE_DIR / "translate_progress.json"
TRANSLATE_LOCK = STATE_DIR / "translate.lock"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  project_id     TEXT PRIMARY KEY,
  slug           TEXT NOT NULL,
  project_type   TEXT,
  title          TEXT NOT NULL,
  description    TEXT,
  downloads      INTEGER DEFAULT 0,
  date_modified  TEXT,
  fetched_at     TEXT NOT NULL,
  title_zh       TEXT,
  description_zh TEXT,
  title_status   TEXT NOT NULL DEFAULT 'pending',
  description_status TEXT NOT NULL DEFAULT 'pending',
  translate_error TEXT,
  translated_at  TEXT
);

CREATE TABLE IF NOT EXISTS crawl_checkpoint (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  offset INTEGER NOT NULL DEFAULT 0,
  total_hits INTEGER,
  inserted INTEGER NOT NULL DEFAULT 0,
  skipped_dup INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS translate_progress (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  provider TEXT,
  done_titles INTEGER NOT NULL DEFAULT 0,
  done_descriptions INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  chars_sent INTEGER NOT NULL DEFAULT 0,
  requests INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  updated_at TEXT,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_type ON projects(project_type);
CREATE INDEX IF NOT EXISTS idx_projects_downloads ON projects(downloads DESC);
CREATE INDEX IF NOT EXISTS idx_title_status ON projects(title_status);
CREATE INDEX IF NOT EXISTS idx_desc_status ON projects(description_status);
"""


def safe_type(name: str | None) -> str:
    raw = (name or "unknown").strip() or "unknown"
    cleaned = SAFE_TYPE.sub("_", raw)
    return cleaned or "unknown"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dirs() -> None:
    for p in (
        ROOT / "data",
        SOURCE_DIR,
        REVIEW_TITLES,
        STATE_DIR,
        DIST_DIR,
    ):
        p.mkdir(parents=True, exist_ok=True)


def connect_db(path: Path = CATALOG_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO crawl_checkpoint "
        "(id, offset, inserted, skipped_dup, done) VALUES (1, 0, 0, 0, 0)"
    )
    conn.execute("INSERT OR IGNORE INTO translate_progress (id) VALUES (1)")
    migrate_columns(conn)
    conn.commit()
    return conn


def migrate_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    for name, typ in [
        ("title_zh", "TEXT"),
        ("description_zh", "TEXT"),
        ("title_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("description_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("translate_error", "TEXT"),
        ("translated_at", "TEXT"),
    ]:
        if name not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {typ}")


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


ALLOWED_STATUS = {"pending", "ai", "reviewed", "skip", "error", "machine", "done"}


def _nonempty(s: str | None) -> str:
    return (s or "").strip()


def effective_zh(row: dict) -> str:
    """人工 > AI > 底稿；兼容旧字段 zh。"""
    for key in ("zh_human", "zh_ai", "zh_draft", "zh"):
        v = _nonempty(row.get(key))
        if v:
            return v
    return ""


def needs_ai_correct(row: dict) -> bool:
    """尚无 AI 纠正、非 skip、且人工未定稿。"""
    status = str(row.get("status") or "").strip()
    if status == "skip":
        return False
    if _nonempty(row.get("zh_human")):
        return False
    if _nonempty(row.get("zh_ai")):
        return False
    return True


def parse_review_line(line: str) -> dict | None:
    """解析 JSONL：三层译名 + status。"""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    try:
        o = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(o, dict):
        return None
    pid = str(o.get("id") or "").strip()
    en = o.get("en")
    if en is None:
        en = o.get("title_en") or o.get("title") or ""
    en = str(en)

    zh_legacy = o.get("zh")
    if zh_legacy is None:
        zh_legacy = o.get("title_zh") or ""
    zh_legacy = str(zh_legacy)

    zh_draft = o.get("zh_draft")
    zh_ai = o.get("zh_ai")
    zh_human = o.get("zh_human")
    # 旧单字段：迁入底稿语义（读时兼容，写时由 migrate 落盘）
    if zh_draft is None and zh_ai is None and zh_human is None:
        zh_draft = zh_legacy
        zh_ai = ""
        zh_human = ""
    else:
        zh_draft = "" if zh_draft is None else str(zh_draft)
        zh_ai = "" if zh_ai is None else str(zh_ai)
        zh_human = "" if zh_human is None else str(zh_human)

    desc = o.get("desc")
    if desc is None:
        desc = o.get("desc_en") or o.get("description") or ""
    desc_zh = o.get("desc_zh")
    if desc_zh is None:
        desc_zh = o.get("description_zh") or ""
    desc = str(desc)
    desc_zh = str(desc_zh)

    status = str(o.get("status") or "pending").strip()
    if status not in ALLOWED_STATUS:
        return None
    if status == "done":
        status = "pending"
    if status == "machine":
        status = "pending"
    # 有人工则 reviewed；有 AI 无人工则 ai；否则 pending
    if _nonempty(zh_human):
        status = "reviewed" if status != "skip" else status
    elif _nonempty(zh_ai) and status not in ("skip", "error"):
        status = "ai"

    if not pid or not en:
        return None

    row = {
        "id": pid,
        "en": en,
        "zh_draft": zh_draft,
        "zh_ai": zh_ai,
        "zh_human": zh_human,
        "zh": effective_zh(
            {
                "zh_human": zh_human,
                "zh_ai": zh_ai,
                "zh_draft": zh_draft,
                "zh": zh_legacy,
            }
        ),
        "desc": desc,
        "desc_zh": desc_zh,
        "status": status,
    }
    return row


def format_review_line(
    pid: str,
    en: str,
    status: str,
    *,
    zh_draft: str = "",
    zh_ai: str = "",
    zh_human: str = "",
    zh: str | None = None,
    desc: str = "",
    desc_zh: str = "",
) -> str:
    """写入三层字段；zh 为 effective，便于人工扫一眼。"""
    draft = zh_draft or ""
    ai = zh_ai or ""
    human = zh_human or ""
    # 兼容旧调用：只传了 zh=
    if zh is not None and not draft and not ai and not human:
        draft = zh
    eff = effective_zh(
        {"zh_human": human, "zh_ai": ai, "zh_draft": draft}
    )
    obj: dict = {
        "id": pid,
        "en": en or "",
        "zh_draft": draft,
        "zh_ai": ai,
        "zh_human": human,
        "zh": eff,
        "status": status,
    }
    if desc:
        obj["desc"] = desc
    if desc_zh:
        obj["desc_zh"] = desc_zh
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def load_all_review_titles(review_root: Path = REVIEW_TITLES) -> dict[str, dict]:
    """id -> row。reviewed（有 zh_human）优先保留。"""
    out: dict[str, dict] = {}
    if not review_root.is_dir():
        return out
    for path in sorted(review_root.rglob("*.jsonl")):
        rel = path.relative_to(review_root)
        ptype = rel.parts[0] if len(rel.parts) > 1 else "unknown"
        shard = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            row = parse_review_line(line)
            if not row:
                continue
            row["path"] = str(path)
            row["type"] = ptype
            row["shard"] = shard
            prev = out.get(row["id"])
            if prev and prev.get("status") == "reviewed" and row["status"] != "reviewed":
                continue
            out[row["id"]] = row
    return out
