# 贡献指南

只改 [`review/titles/`](review/titles/) 下的 `.jsonl`。

```json
{"id":"AANobbMI","en":"Sodium","zh_draft":"钠","zh_ai":"钠","zh_human":"","zh":"钠","status":"ai"}
```

| 字段 | 含义 |
|------|------|
| `id` | 项目 ID，勿改 |
| `en` | 英文名，一般勿改 |
| `zh_draft` | 待纠正底稿（旧机翻会迁到这里） |
| `zh_ai` | AI 纠正结果 |
| `zh_human` | 人工纠正（最高优先） |
| `zh` | 有效译名 = human ?? ai ?? draft（可自动生成） |
| `status` | `pending` / `ai` / `reviewed` / `skip` |

人工校对：填写 `zh_human`，并把 `status` 设为 `reviewed`。  
不要改 `source/`、`dist/`、`state/`。

提交前：`python scripts/validate.py`
