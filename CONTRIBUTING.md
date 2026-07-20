# 贡献指南

只改 [`review/titles/`](review/titles/) 下的 `.jsonl`。

```json
{"id":"AANobbMI","en":"Sodium","zh":"钠","status":"reviewed"}
```

| 字段 | 含义 |
|------|------|
| `id` | 项目 ID，勿改 |
| `en` | 英文名，一般勿改 |
| `zh` | 中文名 |
| `status` | `pending` / `machine` / `reviewed` / `skip` |

校对后改 `status` 为 `reviewed`。不要改 `source/`、`dist/`、`state/`。

提交前：`python scripts/validate.py`
