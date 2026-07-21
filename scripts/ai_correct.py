#!/usr/bin/env python3
"""多供应商 AI 纠正尚无 zh_ai 的标题（社区向中文名）。

环境变量：
  GOOGLE_API_KEY   — Google Gemini
  BIGMODEL_API_KEY — 智谱 BigModel

策略：轮流使用可用供应商；某个 429/限流则跳过，换下一个；全部限流则结束本小时。
严格串行：等本批完整回答并写盘后，再发起下一批。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    REVIEW_TITLES,
    STATE_DIR,
    ensure_dirs,
    format_review_line,
    needs_ai_correct,
    now_iso,
    parse_review_line,
    write_json,
)

DEFAULT_BATCH = 40
PROGRESS = STATE_DIR / "ai_correct_progress.json"

SYSTEM_PROMPT = """你是 Minecraft Java 模组/整合包/资源中文译名专家，熟悉 MC百科(mcmod.cn) 与国内社区通行译名。

## 任务
把每条英文标题译成社区认知中的中文名；若无通行译名，再按意思简短意译。

## 输出格式（必须严格遵守，否则视为失败）
1. 只输出一个 JSON 数组本体，禁止 markdown 代码块（禁止 ```），禁止任何解释、注释、前后缀文字。
2. 数组长度必须与输入条数完全相同，顺序与输入一一对应，不得合并、跳过或追加。
3. 每项必须是 JSON 对象，且只能包含三个字段：id、en、zh（不得使用 translation、title_zh 等其它字段名）。
4. id：必须与输入中的 id 逐字复制，不得省略、修改、重新生成或留空。
5. en：必须与输入中的 en 原样回显，不得改写。
6. zh：非空中文字符串，尽量短，像启动器/百科列表里显示的名字；不要加书名号、引号或英文后缀。

## 译名原则
- 有社区通行译名（如 Create→机械动力、JEI→Just Enough Items 常保留或音译）优先用通行名。
- 专有名词可音译或保留常见写法；SMP/Mod/API 等可按社区习惯处理。
- 不要翻译 id；不要把 en 填进 zh。

## 示例
输入：[{"id":"W0RlaT0h","en":"Create"}, {"id":"W1hcf7F7","en":"Hardcore SMP"}]
输出：[{"id":"W0RlaT0h","en":"Create","zh":"机械动力"}, {"id":"W1hcf7F7","en":"Hardcore SMP","zh":"极限生存"}]
"""

BIGMODEL_SYSTEM_PROMPT = (
    "你是严格的 JSON 翻译 API。只输出合法 JSON 数组，不要 markdown，不要解释。"
    "每项必须含 id、en、zh 三个字段；id/en 与输入完全一致；数组长度与顺序与输入相同。"
)


def _build_user_prompt(titles: list[dict]) -> str:
    n = len(titles)
    return (
        f"请翻译以下 {n} 条，输出恰好 {n} 项的 JSON 数组（每项含 id、en、zh）：\n"
        f"{json.dumps(titles, ensure_ascii=False)}"
    )


@dataclass
class Provider:
    name: str
    model: str
    api_key: str
    cooled: bool = False
    ok_batches: int = 0
    fail_batches: int = 0


@dataclass
class RotateState:
    providers: list[Provider] = field(default_factory=list)
    index: int = 0

    def alive(self) -> list[Provider]:
        return [p for p in self.providers if p.api_key and not p.cooled]

    def next_provider(self) -> Provider | None:
        alive = self.alive()
        if not alive:
            return None
        # 轮询：从当前 index 起找下一个可用
        n = len(self.providers)
        for _ in range(n):
            p = self.providers[self.index % n]
            self.index = (self.index + 1) % n
            if p.api_key and not p.cooled:
                return p
        return None

    def mark_rate_limited(self, p: Provider) -> None:
        p.cooled = True
        p.fail_batches += 1
        print(f"provider {p.name} rate-limited/unavailable — skip until next run", flush=True)


def list_pending(review_root: Path) -> list[tuple[Path, int, dict]]:
    items: list[tuple[Path, int, dict]] = []
    for path in sorted(review_root.rglob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            row = parse_review_line(line)
            if not row:
                continue
            if needs_ai_correct(row):
                items.append((path, idx, row))
    return items


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    elif text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    # 有时模型外包一层对象
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        for key in ("translations", "items", "data", "result"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValueError(f"unexpected response type: {type(parsed)}")
    out: list[dict] = []
    for item in parsed:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append({"zh": item.strip()})
    return out


def _extract_zh(item: dict) -> str:
    for key in ("zh", "title_zh", "chinese", "translation", "zh_cn", "name_zh", "cn"):
        v = str(item.get(key) or "").strip()
        if v:
            return v
    return ""


def _index_translation_results(
    results: list[dict], titles: list[dict]
) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict] = {}
    by_en: dict[str, dict] = {}
    for x in results:
        if not isinstance(x, dict):
            continue
        pid = str(x.get("id") or "").strip()
        en = str(x.get("en") or x.get("title") or x.get("title_en") or "").strip()
        if pid:
            by_id[pid] = x
        if en:
            by_en[en.casefold()] = x

    id_hits = sum(1 for t in titles if str(t.get("id") or "") in by_id)
    if len(results) == len(titles) and id_hits < max(1, len(titles) // 2):
        for i, t in enumerate(titles):
            pid = str(t.get("id") or "")
            if pid and pid not in by_id and i < len(results) and isinstance(results[i], dict):
                by_id[pid] = results[i]
    return by_id, by_en


def _lookup_translation(
    row: dict, by_id: dict[str, dict], by_en: dict[str, dict]
) -> tuple[dict | None, str]:
    pid = str(row.get("id") or "")
    hit = by_id.get(pid)
    if hit is None:
        en_key = str(row.get("en") or "").casefold()
        if en_key:
            hit = by_en.get(en_key)
    if hit is None:
        return None, ""
    return hit, _extract_zh(hit)


def gemini_translate(titles: list[dict], api_key: str, model: str) -> list[dict]:
    prompt = SYSTEM_PROMPT + "\n\n" + _build_user_prompt(titles)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_array(text)


def bigmodel_translate(titles: list[dict], api_key: str, model: str) -> list[dict]:
    """智谱 OpenAI 兼容：https://open.bigmodel.cn/api/paas/v4/chat/completions"""
    user_prompt = SYSTEM_PROMPT + "\n\n" + _build_user_prompt(titles)
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": BIGMODEL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"]
    return _parse_json_array(text)


def translate_with(provider: Provider, titles: list[dict]) -> list[dict]:
    if provider.name == "google":
        return gemini_translate(titles, provider.api_key, provider.model)
    if provider.name == "bigmodel":
        return bigmodel_translate(titles, provider.api_key, provider.model)
    raise ValueError(f"unknown provider {provider.name}")


def is_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (429, 403):
            return True
        # 智谱有时用 1302 等业务码，仍会 HTTP 200；HTTP 层先看 429/403
        return False
    msg = str(exc).lower()
    return any(x in msg for x in ("rate", "quota", "429", "限流", "频率", "exceeded"))


def apply_updates(updates: list[tuple[Path, int, dict]]) -> None:
    by_path: dict[Path, dict[int, dict]] = {}
    for path, idx, row in updates:
        by_path.setdefault(path, {})[idx] = row
    for path, idx_map in by_path.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, row in idx_map.items():
            if 0 <= idx < len(lines):
                lines[idx] = format_review_line(
                    row["id"],
                    row["en"],
                    "ai",
                    zh_draft=row.get("zh_draft") or "",
                    zh_ai=row.get("zh_ai") or "",
                    zh_human=row.get("zh_human") or "",
                    desc=row.get("desc") or "",
                    desc_zh=row.get("desc_zh") or "",
                )
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_rotator() -> RotateState:
    # 智谱免费额度更适合长跑；Google 免费档常 20 RPM，放后面
    return RotateState(
        providers=[
            Provider(
                name="bigmodel",
                model=os.environ.get("BIGMODEL_MODEL", "glm-4-flash").strip()
                or "glm-4-flash",
                api_key=(os.environ.get("BIGMODEL_API_KEY") or "").strip(),
            ),
            Provider(
                name="google",
                model=os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash").strip()
                or "gemini-2.5-flash",
                api_key=(os.environ.get("GOOGLE_API_KEY") or "").strip(),
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-correct pending MCDB titles (multi-provider)")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--duration-minutes", type=float, default=55.0)
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between batches")
    parser.add_argument("--limit", type=int, default=0, help="max items this run (0=unlimited)")
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="stop after N API batches (0=unlimited; use 1 for incremental CI push)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rotator = build_rotator()
    configured = [p.name for p in rotator.providers if p.api_key]
    missing = [p.name for p in rotator.providers if not p.api_key]
    print(
        f"providers configured={configured or '[]'} missing_key={missing or '[]'}",
        flush=True,
    )
    if not args.dry_run and not rotator.alive():
        print(
            "ERROR: set at least one of GOOGLE_API_KEY / BIGMODEL_API_KEY",
            file=sys.stderr,
        )
        return 2
    if not args.dry_run and "bigmodel" in missing:
        print(
            "WARN: BIGMODEL_API_KEY not set — cannot failover when Google is rate-limited",
            flush=True,
        )
    if not args.dry_run and "google" in missing:
        print(
            "WARN: GOOGLE_API_KEY not set — only BigModel will be used",
            flush=True,
        )

    ensure_dirs()
    deadline = time.time() + max(0.1, args.duration_minutes) * 60.0
    done = 0
    failed = 0
    batches = 0
    started = now_iso()
    last_provider = ""

    while time.time() < deadline:
        if args.limit and done >= args.limit:
            break
        pending = list_pending(REVIEW_TITLES)
        if not pending:
            print("no pending items")
            break
        take = args.batch
        if args.limit:
            take = min(take, args.limit - done)
        chunk = pending[:take]
        titles = [{"id": r["id"], "en": r["en"]} for _, _, r in chunk]

        if args.dry_run:
            print(json.dumps({"providers": [p.name for p in rotator.alive()], "titles": titles}, ensure_ascii=False, indent=2))
            done += len(titles)
            batches += 1
            break

        # 本批：尝试轮询各供应商，直到成功或全部限流
        results: list[dict] | None = None
        used: Provider | None = None
        while True:
            provider = rotator.next_provider()
            if provider is None:
                cooled = [p.name for p in rotator.providers if p.cooled]
                no_key = [p.name for p in rotator.providers if not p.api_key]
                print(
                    "no available providers — stop this hour "
                    f"(cooled={cooled}, missing_key={no_key})",
                    flush=True,
                )
                write_json(
                    PROGRESS,
                    {
                        "started_at": started,
                        "finished_at": now_iso(),
                        "done": done,
                        "failed": failed,
                        "batches": batches,
                        "stop_reason": "no_available_providers",
                        "cooled": cooled,
                        "missing_key": no_key,
                        "providers": [
                            {
                                "name": p.name,
                                "model": p.model,
                                "cooled": p.cooled,
                                "ok_batches": p.ok_batches,
                                "fail_batches": p.fail_batches,
                                "configured": bool(p.api_key),
                            }
                            for p in rotator.providers
                        ],
                    },
                )
                print(json.dumps({"done": done, "failed": failed, "batches": batches}, indent=2))
                return 0

            print(
                f"batch={batches+1} size={len(titles)} remaining~={len(pending)} "
                f"— {provider.name}/{provider.model} (wait reply)…",
                flush=True,
            )
            try:
                results = translate_with(provider, titles)
                used = provider
                provider.ok_batches += 1
                print(
                    f"batch={batches+1} reply ok via {provider.name} items={len(results)}",
                    flush=True,
                )
                break
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", errors="replace")
                print(f"{provider.name} HTTP {e.code}: {err[:400]}", file=sys.stderr)
                if is_rate_limit_error(e) or e.code in (429, 403):
                    rotator.mark_rate_limited(provider)
                    continue
                provider.fail_batches += 1
                failed += len(titles)
                time.sleep(max(args.delay, 5.0))
                results = None
                break
            except Exception as e:
                print(f"{provider.name} ERROR: {e}", file=sys.stderr)
                if is_rate_limit_error(e):
                    rotator.mark_rate_limited(provider)
                    continue
                provider.fail_batches += 1
                failed += len(titles)
                time.sleep(max(args.delay, 5.0))
                results = None
                break

        if results is None or used is None:
            continue

        last_provider = f"{used.name}/{used.model}"
        by_id, by_en = _index_translation_results(results, titles)
        updates: list[tuple[Path, int, dict]] = []
        for path, idx, row in chunk:
            hit, zh = _lookup_translation(row, by_id, by_en)
            if not zh:
                failed += 1
                continue
            row = dict(row)
            row["zh_ai"] = zh
            row["status"] = "ai"
            updates.append((path, idx, row))
            done += 1

        if not updates and results:
            sample = results[0] if isinstance(results[0], dict) else {"raw": results[0]}
            print(
                f"WARN: 0/{len(chunk)} translations matched; "
                f"sample={json.dumps(sample, ensure_ascii=False)[:300]}",
                flush=True,
            )
        elif updates:
            apply_updates(updates)
            print(f"batch={batches+1} wrote zh_ai={len(updates)} via {last_provider}", flush=True)
        batches += 1
        write_json(
            PROGRESS,
            {
                "started_at": started,
                "updated_at": now_iso(),
                "last_provider": last_provider,
                "done": done,
                "failed": failed,
                "batches": batches,
                "providers": [
                    {
                        "name": p.name,
                        "model": p.model,
                        "cooled": p.cooled,
                        "ok_batches": p.ok_batches,
                        "fail_batches": p.fail_batches,
                        "configured": bool(p.api_key),
                    }
                    for p in rotator.providers
                ],
            },
        )
        if args.max_batches and batches >= args.max_batches:
            print(f"max_batches={args.max_batches} reached — stop", flush=True)
            break
        if time.time() >= deadline:
            break
        time.sleep(args.delay)

    summary = {
        "started_at": started,
        "finished_at": now_iso(),
        "last_provider": last_provider,
        "done": done,
        "failed": failed,
        "batches": batches,
        "dry_run": args.dry_run,
        "providers": [
            {
                "name": p.name,
                "model": p.model,
                "cooled": p.cooled,
                "ok_batches": p.ok_batches,
                "fail_batches": p.fail_batches,
                "configured": bool(p.api_key),
            }
            for p in rotator.providers
        ],
    }
    write_json(PROGRESS, summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
