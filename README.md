# MCDB

基于 API 的自动化中英译名库：抓取 → 待纠正 → AI 纠正 → 人工纠正（层层替换）。

## 译名管道

```text
crawl（只补新 id）
  → review：zh_draft 底稿，status=pending
  → ai_correct（每小时，约跑 55 分钟）：只填尚无 zh_ai 的条目
  → 人工：写 zh_human → reviewed
有效 zh = zh_human ?? zh_ai ?? zh_draft
```

## 发行包

- 最新：https://github.com/AstralNext/MCDB/releases/tag/latest-dist

| 文件 | 用途 |
|------|------|
| `bilingual.jsonl` | 三层译名 + 有效 `zh` |
| `exact_titles.json` | 英文 → 有效中文 |
| `mcdb-*.zip` | 完整包 |

## 自动化

| 工作流 | 作用 |
|--------|------|
| `crawl-daily` | API 爬取；review 只追加新 id |
| `ai-correct-hourly` | Gemini 纠正待处理标题（需 `GOOGLE_API_KEY` secret） |
| `compile-daily` | 编译 JSON 产物 |
| `release-compile` | 打包发布 |
| `maintain-weekly` | 校验 + 重编译 |

## 本地

```bash
python scripts/migrate_zh_layers.py   # 旧数据迁三层（幂等）
python scripts/ai_correct.py --limit 10
python scripts/compile_dist.py
python scripts/search_titles.py "钠" -k 5
```

密钥（GitHub Actions → Settings → Secrets and variables → Actions）：

| Name | 内容 |
|------|------|
| `GOOGLE_API_KEY` | Google AI Studio 密钥 |
| `BIGMODEL_API_KEY` | 智谱主密钥 |
| `BIGMODEL2_API_KEY` | 第二个智谱密钥（可选，并行） |
| `AGNES_API_KEY` | Agnes AI 主密钥 |
| `AGNES2_API_KEY` | 第二个 Agnes 密钥（可选，并行） |
| `AGNES3_API_KEY` | 第三个 Agnes 密钥（可选，并行） |

可选：`GOOGLE_MODEL`（默认 `gemini-2.5-flash`）、`BIGMODEL_MODEL`（默认 `glm-4-flash`）、`AGNES_MODEL`（默认 `agnes-2.0-flash`）。  
也可用 `BIGMODEL_API_KEYS` / `AGNES_API_KEYS`（换行或逗号分隔多个密钥）。  
并行线程数默认 = 已配置密钥数；可用环境变量 `AI_CORRECT_WORKERS` 覆盖。
