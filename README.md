# MCDB

基于 API 的自动化中英译名库：定时爬取、机翻、人工校对，数据全为 JSON/JSONL。

## 发行包

- 最新：https://github.com/AstralNext/MCDB/releases/tag/latest-dist

| 文件 | 用途 |
|------|------|
| `bilingual.jsonl` | 中英对照（标题 → 译名 / 模糊搜索） |
| `exact_titles.json` | 英文 → 中文精确替换 |
| `mcdb-*.zip` | 完整包 |

> 已不再编译语义向量。

## 目录

```text
source/               # 英文源（自动维护）
review/titles/        # 人工校对
dist/                 # 编译产物
state/                # 进度
scripts/
.github/workflows/
```

## 校对格式

```json
{"id":"AANobbMI","en":"Sodium","zh":"钠","status":"reviewed"}
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 自动化

| 工作流 | 作用 |
|--------|------|
| `crawl-daily` | API 爬取 → `source/` + `review/` |
| `translate-hourly` | API 机翻一批 |
| `compile-daily` | 编译 JSON 产物 |
| `release-compile` | 打包发布 |
| `maintain-weekly` | 校验 + 重编译 |

## 本地

```bash
python scripts/crawl.py
python scripts/translate_edge.py --batch 20
python scripts/compile_dist.py
python scripts/search_titles.py "钠" -k 5
```
