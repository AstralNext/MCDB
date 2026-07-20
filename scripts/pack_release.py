#!/usr/bin/env python3
"""打包发行目录：全 JSON（对照 + 精确表 + 语义分片），打 zip。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DIST_DIR, ROOT, ensure_dirs, now_iso, write_json

RELEASE_DIR = ROOT / "release"

README_TXT = """MCDB 发行包（全 JSON，无数据库）
================================

文件
----
- bilingual.jsonl       中英对照
- exact_titles.json     英文 → 中文精确表
- semantic/*.jsonl      语义向量分片（字段 v 为 base64 float32）
- semantic_meta.json    语义元信息
- version.json          版本

搜索示例
--------
python scripts/search_semantic.py --semantic ./semantic "钠" -k 5
python scripts/exact_replace.py --map exact_titles.json "Sodium"
"""

USAGE_MD = """# MCDB 发行包用法（JSON only）

## 中英对照

`bilingual.jsonl` 每行：

```json
{"id":"AANobbMI","slug":"sodium","type":"mod","en":"Sodium","zh":"钠","status":"machine"}
```

## 精确替换

`exact_titles.json` → `by_en` / `by_id`

## 向量搜索

`semantic/*.jsonl` 含字段 `v`（base64 编码的 float32 向量）。

```bash
python scripts/search_semantic.py --semantic path/to/semantic "高性能渲染" -k 5
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Pack MCDB JSON release")
    parser.add_argument("--dist", type=Path, default=DIST_DIR)
    parser.add_argument("--out", type=Path, default=RELEASE_DIR)
    args = parser.parse_args()

    ensure_dirs()
    required = [
        args.dist / "version.json",
        args.dist / "bilingual.jsonl",
        args.dist / "exact_titles.json",
        args.dist / "semantic_meta.json",
        args.dist / "semantic",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("缺少文件，请先 python scripts/compile_dist.py:", *missing, sep="\n  ")
        return 2

    version = json.loads((args.dist / "version.json").read_text(encoding="utf-8"))
    ver = version.get("version") or "unknown"
    stamp = (version.get("built_at") or now_iso())[:10].replace("-", "")

    if args.out.exists():
        shutil.rmtree(args.out)
    stage = args.out / "mcdb"
    stage.mkdir(parents=True)

    for name in (
        "version.json",
        "bilingual.jsonl",
        "exact_titles.json",
        "semantic_meta.json",
    ):
        shutil.copy2(args.dist / name, stage / name)
    shutil.copytree(args.dist / "semantic", stage / "semantic")

    (stage / "README.txt").write_text(README_TXT, encoding="utf-8")
    (stage / "USAGE.md").write_text(USAGE_MD, encoding="utf-8")

    zip_name = f"mcdb-{stamp}-{ver}.zip"
    zip_path = args.out / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"mcdb/{path.relative_to(stage).as_posix()}")

    for name in (
        "bilingual.jsonl",
        "exact_titles.json",
        "version.json",
        "semantic_meta.json",
    ):
        shutil.copy2(args.dist / name, args.out / name)
    shutil.copytree(args.dist / "semantic", args.out / "semantic")

    manifest = {
        "tag_hint": f"dist-{stamp}-{ver}",
        "zip": zip_name,
        "built_at": version.get("built_at"),
        "version": ver,
        "pair_count": version.get("pair_count"),
        "format": "json-only",
        "assets": [
            zip_name,
            "bilingual.jsonl",
            "exact_titles.json",
            "semantic_meta.json",
            "semantic/",
        ],
    }
    write_json(args.out / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
