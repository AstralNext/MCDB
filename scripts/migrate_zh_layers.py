#!/usr/bin/env python3
"""将旧单字段 zh 迁入三层：zh_draft=旧zh，全部进 pending（skip 除外）。不删译文。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REVIEW_TITLES, format_review_line, parse_review_line


def migrate_file(path: Path, dry_run: bool) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    changed = 0
    kept = 0
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            out.append(line)
            continue
        try:
            o = json.loads(raw)
        except json.JSONDecodeError:
            out.append(line)
            continue
        row = parse_review_line(raw)
        if not row:
            out.append(line)
            continue

        # 已是三层且无旧 machine 语义：仍统一落盘格式
        old_zh = str(o.get("zh") or o.get("title_zh") or "")
        draft = str(o.get("zh_draft") if o.get("zh_draft") is not None else old_zh)
        ai = str(o.get("zh_ai") or "")
        human = str(o.get("zh_human") or "")
        status = str(o.get("status") or "pending")
        if status == "skip":
            new_status = "skip"
        else:
            # 现有全部进待纠正；已有 zh_ai 的保留
            if ai.strip():
                new_status = "ai" if not human.strip() else "reviewed"
            elif human.strip():
                # 按计划：旧 reviewed 也进 pending 等 AI；human 清空进 draft
                if not draft.strip() and human.strip():
                    draft = human
                human = ""
                new_status = "pending"
            else:
                new_status = "pending"
                if not draft.strip() and old_zh.strip():
                    draft = old_zh

        new_line = format_review_line(
            row["id"],
            row["en"],
            new_status,
            zh_draft=draft,
            zh_ai=ai,
            zh_human=human,
            desc=row.get("desc") or "",
            desc_zh=row.get("desc_zh") or "",
        )
        if new_line != raw:
            changed += 1
        else:
            kept += 1
        out.append(new_line)

    if not dry_run and changed:
        path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return changed, kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate review titles to 3-layer schema")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=REVIEW_TITLES)
    args = parser.parse_args()

    total_c = total_k = 0
    files = 0
    for path in sorted(args.root.rglob("*.jsonl")):
        c, k = migrate_file(path, args.dry_run)
        total_c += c
        total_k += k
        files += 1
        if c:
            print(f"{path.relative_to(args.root)} changed={c}")
    print(
        json.dumps(
            {
                "files": files,
                "lines_changed": total_c,
                "lines_same": total_k,
                "dry_run": args.dry_run,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
