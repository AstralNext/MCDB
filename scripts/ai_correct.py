#!/usr/bin/env python3
"""用 Google Gemini 纠正尚无 zh_ai 的标题（社区向中文名）。

环境变量：GOOGLE_API_KEY
默认：每批 10 条，持续约 duration-minutes（工作流按 1 小时跑）。
只处理 needs_ai_correct（无 zh_ai、无 zh_human、非 skip）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    REVIEW_TITLES,
    STATE_DIR,
    ensure_dirs,
    format_review_line,
    needs_ai_correct,
    now_iso,
    parse_review_line,
    write_json,
)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_BATCH = 40
PROGRESS = STATE_DIR / "ai_correct_progress.json"

SYSTEM_PROMPT = (
    "你是 Minecraft Java 模组中文译名专家，熟悉 MC百科(mcmod.cn) 与国内社区通行译名。\n"
    "任务：把英文标题译成符合社区认知的中文名；若无通行译名，再按意思意译。\n"
    "规则：\n"
    "- 只输出 JSON 数组，不要 markdown，不要解释\n"
    "- 每项字段：id, en, zh\n"
    "- zh 尽量短，像模组列表里显示的名字\n"
)


def list_pending(review_root: Path) -> list[tuple[Path, int, dict]]:
    """(path, line_index, row) 待 AI 纠正。"""
    items: list[tuple[Path, int, dict]] = []
    for path in sorted(review_root.rglob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            row = parse_review_line(line)
            if not row:
                continue
            if needs_ai_correct(row):
                items.append((path, idx, row))
    return items


def gemini_translate(titles: list[dict], api_key: str, model: str) -> list[dict]:
    prompt = SYSTEM_PROMPT + f"输入：\n{json.dumps(titles, ensure_ascii=False)}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    parsed = json.loads(text)
    if isinstance(parsed, dict) and "translations" in parsed:
        parsed = parsed["translations"]
    if not isinstance(parsed, list):
        raise ValueError(f"unexpected response type: {type(parsed)}")
    return parsed


def apply_updates(updates: list[tuple[Path, int, dict]]) -> None:
    """updates: path, idx, full row dict with zh_ai set."""
    by_path: dict[Path, dict[int, dict]] = {}
    for path, idx, row in updates:
        by_path.setdefault(path, {})[idx] = row
    for path, idx_map in by_path.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, row in idx_map.items():
            if 0 <= idx < len(lines):
                lines[idx] = format_review_line(
                    row["id"],
                    row["en"],
                    "ai",
                    zh_draft=row.get("zh_draft") or "",
                    zh_ai=row.get("zh_ai") or "",
                    zh_human=row.get("zh_human") or "",
                    desc=row.get("desc") or "",
                    desc_zh=row.get("desc_zh") or "",
                )
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-correct pending MCDB titles via Gemini")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--duration-minutes", type=float, default=55.0)
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between batches")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0, help="max items this run (0=unlimited)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not api_key and not args.dry_run:
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        return 2

    ensure_dirs()
    deadline = time.time() + max(0.1, args.duration_minutes) * 60.0
    done = 0
    failed = 0
    batches = 0
    started = now_iso()

    while time.time() < deadline:
        if args.limit and done >= args.limit:
            break
        pending = list_pending(REVIEW_TITLES)
        if not pending:
            print("no pending items")
            break
        take = args.batch
        if args.limit:
            take = min(take, args.limit - done)
        chunk = pending[:take]
        titles = [{"id": r["id"], "en": r["en"]} for _, _, r in chunk]
        print(f"batch={batches+1} size={len(titles)} remaining~={len(pending)}", flush=True)

        if args.dry_run:
            print(json.dumps(titles, ensure_ascii=False, indent=2))
            done += len(titles)
            batches += 1
            break

        try:
            results = gemini_translate(titles, api_key, args.model)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code}: {err[:500]}", file=sys.stderr)
            failed += len(titles)
            # 配额类错误：结束本小时，下小时再试
            if e.code in (429, 403):
                break
            time.sleep(max(args.delay, 5.0))
            continue
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            failed += len(titles)
            time.sleep(max(args.delay, 5.0))
            continue

        by_id = {str(x.get("id")): x for x in results if isinstance(x, dict)}
        updates: list[tuple[Path, int, dict]] = []
        for path, idx, row in chunk:
            hit = by_id.get(row["id"])
            zh = ""
            if hit:
                zh = str(hit.get("zh") or "").strip()
            if not zh:
                failed += 1
                continue
            row = dict(row)
            row["zh_ai"] = zh
            row["status"] = "ai"
            updates.append((path, idx, row))
            done += 1

        if updates:
            apply_updates(updates)
        batches += 1
        write_json(
            PROGRESS,
            {
                "started_at": started,
                "updated_at": now_iso(),
                "model": args.model,
                "done": done,
                "failed": failed,
                "batches": batches,
            },
        )
        if time.time() >= deadline:
            break
        time.sleep(args.delay)

    summary = {
        "started_at": started,
        "finished_at": now_iso(),
        "model": args.model,
        "done": done,
        "failed": failed,
        "batches": batches,
        "dry_run": args.dry_run,
    }
    write_json(PROGRESS, summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
