# MCDB

Modrinth 项目中文译名协作库。全链路 **JSON/JSONL**（不使用数据库文件，避免仓库与工作流体积过大）。

## 发行包

- 滚动最新：https://github.com/AstralNext/MCDB/releases/tag/latest-dist  
- 按日版本：Releases 里的 `dist-YYYYMMDD-…`

| 文件 | 用途 |
|------|------|
| `bilingual.jsonl` | 中英对照 |
| `exact_titles.json` | 英文 → 中文精确替换 |
| `semantic/*.jsonl` + `semantic-*.zip` | 向量搜索（JSON 分片） |
| `mcdb-*.zip` | 完整包 |

## 目录

```text
source/                 # 英文源 JSONL
review/titles/<type>/   # 校对 JSONL：{"id","en","zh","status"}
dist/
  bilingual.jsonl
  exact_titles.json
  semantic_meta.json
  semantic/             # 向量 JSONL（gitignore，走 Release）
state/                  # 进度 JSON
scripts/
.github/workflows/
```

## 人工校对

```json
{"id":"AANobbMI","en":"Sodium","zh":"钠","status":"reviewed"}
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 自动化（均无 DB）

| 工作流 | 作用 |
|--------|------|
| `crawl-daily` | 爬取 → 写 `source/` + `review/` JSONL |
| `translate-hourly` | Edge 机翻 → 只改 `review/` |
| `compile-daily` | 编译 JSON 产物 |
| `release-compile` | 打包并发布 Release |
| `maintain-weekly` | 校验 + 重编译 |

## 本地

```bash
python scripts/crawl.py
python scripts/translate_edge.py --batch 20
python scripts/compile_dist.py
python scripts/pack_release.py
python scripts/search_semantic.py "高性能渲染" -k 5
python scripts/exact_replace.py "Sodium"
```

## 许可

上游项目名与简介版权归原作者 / Modrinth。
