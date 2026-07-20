# 贡献指南

## 只改这里

[`review/titles/`](review/titles/) 下的 `.jsonl` 分片。

一行一条 JSON：

```json
{"id":"AANobbMI","en":"Sodium","zh":"钠","status":"reviewed"}
```

| 字段 | 含义 |
|------|------|
| `id` | Modrinth project_id，勿改 |
| `en` | 英文名（对照用，一般勿改） |
| `zh` | 中文名（主要改这个） |
| `status` | `pending` / `machine` / `reviewed` / `skip` |

校对满意后把 `status` 改成 `reviewed`。不要改 `source/`、`dist/`、`state/`。

## 怎么找未校

- 搜 `"status":"machine"` 或 `"status":"pending"`
- 本地：`python scripts/validate.py`

## PR 建议

1. 一次一个分片，例如 `review/titles/mod/000.jsonl`
2. 标题：`fix(zh): 校对 mod/000 热门译名`
3. 提交前跑 `python scripts/validate.py`

## 专有名词

Sodium、Iris、Fabric、NeoForge 等可保留英文或沿用社区译名；同一 PR 内保持一致。
