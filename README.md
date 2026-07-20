# MCDB

Modrinth 项目中文译名协作库。用 GitHub PR 一起校对译名；机器每日爬新项目、按小时机翻；编译后提供**精确替换**与**语义检索**两类产物。

## 目录

```text
source/                 # 英文源数据（爬虫维护，勿手改）
review/titles/<type>/   # 人工校对 JSONL（改这里）
dist/
  exact_titles.json     # 英文原名 → 中文（翻译替换用）
  version.json          # 构建版本信息
  semantic.sqlite       # 语义库（默认不入库，Actions 产物）
state/                  # 爬取 / 翻译进度
scripts/                # 本地与 CI 脚本
.github/workflows/      # 日爬 / 小时翻 / 日编译 / 周维护
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
| `translate-hourly` | 每小时 | Edge 公共接口翻一批标题；锁 + concurrency 防冲突 |
| `compile-daily` | 每天 | 编译 `exact_titles.json` + `semantic.sqlite` + `version.json` |
| `maintain-weekly` | 每周 | 校验与重编译 |

## 本地命令

```bash
python scripts/crawl.py
python scripts/translate_edge.py --batch 20
python scripts/compile_dist.py
python scripts/validate.py

# 精确替换（翻译用）
python scripts/exact_replace.py "Sodium"

# 语义搜索：中文 → 近义英文（搜索用）
python scripts/search_semantic.py "高性能渲染" -k 5
```

## 产物用途

- **翻译**：`dist/exact_titles.json` 的 `by_en` / `by_id`，按英文原名精确换成中文。
- **搜索**：`semantic.sqlite` 用中文向量近邻，返回对应英文名。

## 许可

协作数据与脚本以仓库为准；上游项目名与简介版权归原作者 / Modrinth。
