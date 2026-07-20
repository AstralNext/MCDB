# MCDB

Modrinth 项目中文译名协作库。用 GitHub PR 一起校对译名；机器每日爬新项目、按小时机翻；编译后提供**中英对照**、**精确替换**与**语义检索**。

## 发行包（推荐下载）

每次编译会打 GitHub Release：

- 滚动最新：https://github.com/AstralNext/MCDB/releases/tag/latest-dist  
- 按日期版本：Releases 列表里的 `dist-YYYYMMDD-…`

包内文件：

| 文件 | 用途 |
|------|------|
| `bilingual.jsonl` | 中英对照（一行一条） |
| `exact_titles.json` | 翻译：英文原名 → 中文 |
| `semantic.sqlite` | 搜索：中文 → 近义英文 |
| `version.json` | 版本与条数 |
| `mcdb-*.zip` | 以上文件的完整压缩包 |

## 目录

```text
source/                 # 英文源数据（爬虫维护，勿手改）
review/titles/<type>/   # 人工校对 JSONL（改这里）
dist/
  bilingual.jsonl       # 中英对照
  exact_titles.json     # 精确替换表
  version.json          # 构建版本
  semantic.sqlite       # 语义库（默认不入库，走 Release）
state/                  # 爬取 / 翻译进度
scripts/
.github/workflows/
```

## 人工校对

`review/titles/mod/000.jsonl` 一行一条：

```json
{"id":"AANobbMI","en":"Sodium","zh":"钠","status":"reviewed"}
```

| status | 含义 |
|--------|------|
| `pending` | 待译 |
| `machine` | 机翻，待人工看 |
| `reviewed` | 人工通过（机翻不会覆盖） |
| `skip` | 不译 |

一次 PR 尽量只改一个分片。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 自动化

| 工作流 | 频率 | 作用 |
|--------|------|------|
| `crawl-daily` | 每天 | 全量扫 Modrinth，只入库新增，追加 review，刷新 `source/` |
| `translate-hourly` | 每小时 | Edge 公共接口翻一批标题 |
| `compile-daily` | 每天 | 编译 dist |
| `release-compile` | 每天 / 手动 | 打包并发布 GitHub Release（对照 + 向量库） |
| `maintain-weekly` | 每周 | 校验与重编译 |

## 本地命令

```bash
python scripts/compile_dist.py
python scripts/pack_release.py
python scripts/validate.py

# 精确替换（翻译用）
python scripts/exact_replace.py "Sodium"

# 语义搜索：中文 → 近义英文（搜索用）
python scripts/search_semantic.py "高性能渲染" -k 5
```

## 许可

协作数据与脚本以仓库为准；上游项目名与简介版权归原作者 / Modrinth。
