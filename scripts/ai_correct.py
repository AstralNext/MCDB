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

SYSTEM_PROMPT = (
    "你是 Minecraft Java 模组中文译名专家，熟悉 MC百科(mcmod.cn) 与国内社区通行译名。\n"
    "任务：把英文标题译成符合社区认知的中文名；若无通行译名，再按意思意译。\n"
    "规则：\n"
    "- 只输出 JSON 数组，不要 markdown，不要解释\n"
    "- 每项字段：id, en, zh\n"
    "- zh 尽量短，像模组列表里显示的名字\n"
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
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
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
    return parsed


def gemini_translate(titles: list[dict], api_key: str, model: str) -> list[dict]:
    prompt = SYSTEM_PROMPT + f"输入：\n{json.dumps(titles, ensure_ascii=False)}"
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
    prompt = SYSTEM_PROMPT + f"输入：\n{json.dumps(titles, ensure_ascii=False)}"
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "只输出合法 JSON 数组，不要其它文字。"},
            {"role": "user", "content": prompt},
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
    return RotateState(
        providers=[
            Provider(
                name="google",
                model=os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash").strip()
                or "gemini-2.5-flash",
                api_key=(os.environ.get("GOOGLE_API_KEY") or "").strip(),
            ),
            Provider(
                name="bigmodel",
                model=os.environ.get("BIGMODEL_MODEL", "glm-4-flash").strip()
                or "glm-4-flash",
                api_key=(os.environ.get("BIGMODEL_API_KEY") or "").strip(),
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-correct pending MCDB titles (multi-provider)")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--duration-minutes", type=float, default=55.0)
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between batches")
    parser.add_argument("--limit", type=int, default=0, help="max items this run (0=unlimited)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rotator = build_rotator()
    if not args.dry_run and not rotator.alive():
        print(
            "ERROR: set at least one of GOOGLE_API_KEY / BIGMODEL_API_KEY",
            file=sys.stderr,
        )
        return 2

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
                print("all providers rate-limited — stop this hour", flush=True)
                write_json(
                    PROGRESS,
                    {
                        "started_at": started,
                        "finished_at": now_iso(),
                        "done": done,
                        "failed": failed,
                        "batches": batches,
                        "stop_reason": "all_providers_cooled",
                        "providers": [
                            {
                                "name": p.name,
                                "model": p.model,
                                "cooled": p.cooled,
                                "ok_batches": p.ok_batches,
                                "fail_batches": p.fail_batches,
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
        by_id = {str(x.get("id")): x for x in results if isinstance(x, dict)}
        updates: list[tuple[Path, int, dict]] = []
        for path, idx, row in chunk:
            hit = by_id.get(row["id"])
            zh = ""
            if hit:
                zh = str(hit.get("zh") or "").strip()
            if not zh:
                failed += 1
                continue
            row = dict(row)
            row["zh_ai"] = zh
            row["status"] = "ai"
            updates.append((path, idx, row))
            done += 1

        if updates:
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
