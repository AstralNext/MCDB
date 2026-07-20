#!/usr/bin/env python3
"""打包发行目录：中英对照 + 精确表 + 语义库 + 说明，并打 zip。"""

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

README_TXT = """MCDB 发行包
===========

本包便于离线使用「中英对照」与「向量搜索」。

文件
----
- bilingual.jsonl     中英对照（一行一条 JSON：id/slug/type/en/zh/status）
- exact_titles.json   精确替换表（by_en / by_id：英文原名 → 中文）
- semantic.sqlite     语义向量库（中文查询 → 近邻英文）
- version.json        版本与条数
- USAGE.md            用法说明

本地搜索示例（需仓库内 scripts）
--------------------------------
python scripts/search_semantic.py --db semantic.sqlite "钠" -k 5
python scripts/exact_replace.py --map exact_titles.json "Sodium"
"""

USAGE_MD = """# MCDB 发行包用法

## 中英对照

读 `bilingual.jsonl`，每行：

```json
{"id":"AANobbMI","slug":"sodium","type":"mod","en":"Sodium","zh":"钠","status":"machine"}
```

适合导入自己的工具、做列表展示、人工浏览。

## 精确替换（翻译用）

读 `exact_titles.json`：

- `by_en["Sodium"]` → `"钠"`
- `by_id["AANobbMI"].zh` → `"钠"`

英文原名完全匹配后替换成中文。

## 向量搜索（搜索用）

使用 `semantic.sqlite`：对中文查询做近邻，返回对应英文名。

若你有本仓库脚本：

```bash
python scripts/search_semantic.py --db path/to/semantic.sqlite "高性能渲染" -k 5
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Pack MCDB release zip")
    parser.add_argument("--dist", type=Path, default=DIST_DIR)
    parser.add_argument("--out", type=Path, default=RELEASE_DIR)
    args = parser.parse_args()

    ensure_dirs()
    required = [
        args.dist / "version.json",
        args.dist / "bilingual.jsonl",
        args.dist / "exact_titles.json",
        args.dist / "semantic.sqlite",
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
        "semantic.sqlite",
    ):
        shutil.copy2(args.dist / name, stage / name)

    (stage / "README.txt").write_text(README_TXT, encoding="utf-8")
    (stage / "USAGE.md").write_text(USAGE_MD, encoding="utf-8")

    zip_name = f"mcdb-{stamp}-{ver}.zip"
    zip_path = args.out / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"mcdb/{path.relative_to(stage).as_posix()}")

    # 也单独放出 sqlite / bilingual，方便只下某一项
    shutil.copy2(args.dist / "semantic.sqlite", args.out / "semantic.sqlite")
    shutil.copy2(args.dist / "bilingual.jsonl", args.out / "bilingual.jsonl")
    shutil.copy2(args.dist / "exact_titles.json", args.out / "exact_titles.json")
    shutil.copy2(args.dist / "version.json", args.out / "version.json")

    manifest = {
        "tag_hint": f"dist-{stamp}-{ver}",
        "zip": zip_name,
        "built_at": version.get("built_at"),
        "version": ver,
        "pair_count": version.get("pair_count"),
        "assets": [
            zip_name,
            "bilingual.jsonl",
            "exact_titles.json",
            "semantic.sqlite",
            "version.json",
        ],
    }
    write_json(args.out / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
