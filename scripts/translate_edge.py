#!/usr/bin/env python3
"""小时任务：Edge 翻译待译标题（只改 review JSONL，无数据库）。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    REVIEW_TITLES,
    TRANSLATE_LOCK,
    TRANSLATE_PROGRESS,
    ensure_dirs,
    format_review_line,
    now_iso,
    parse_review_line,
    read_json,
    write_json,
)

EDGE_AUTH = "https://edge.microsoft.com/translate/auth"
TRANSLATE_URL = "https://api.cognitive.microsofttranslator.com/translate"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
)

DEFAULT_BATCH = 40
DEFAULT_DELAY = 1.8
PROTECTED = {"reviewed", "skip"}


class LockError(RuntimeError):
    pass


def acquire_lock(lock_path: Path, owner: str, stale_sec: int = 7200) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            age = time.time() - float(data.get("ts") or 0)
            if age < stale_sec:
                raise LockError(
                    f"translate lock held by {data.get('owner')} age={age:.0f}s"
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    lock_path.write_text(
        json.dumps({"owner": owner, "ts": time.time(), "at": now_iso()}, indent=2),
        encoding="utf-8",
    )


def release_lock(lock_path: Path, owner: str) -> None:
    if not lock_path.exists():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if data.get("owner") != owner:
            return
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    lock_path.unlink(missing_ok=True)


class EdgeTranslator:
    def __init__(self) -> None:
        self._token = ""
        self._token_at = 0.0

    def _refresh(self, force: bool = False) -> str:
        if self._token and not force and time.time() - self._token_at < 480:
            return self._token
        req = urllib.request.Request(EDGE_AUTH, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            self._token = resp.read().decode("utf-8")
        self._token_at = time.time()
        return self._token

    def translate(self, texts: list[str], to: str = "zh-Hans") -> list[str]:
        if not texts:
            return []
        params = urllib.parse.urlencode(
            {"api-version": "3.0", "from": "en", "to": to}
        )
        url = f"{TRANSLATE_URL}?{params}"
        body = json.dumps([{"Text": t} for t in texts], ensure_ascii=False).encode(
            "utf-8"
        )
        wait = 15.0
        for attempt in range(12):
            token = self._refresh()
            headers = {
                "User-Agent": UA,
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Bearer {token}",
            }
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.load(resp)
                out = [item["translations"][0]["text"] for item in data]
                if len(out) != len(texts):
                    raise RuntimeError("translate length mismatch")
                return out
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:200]
                if e.code in (401, 403):
                    self._refresh(force=True)
                    time.sleep(min(wait, 30))
                    wait = min(wait * 1.5, 300)
                    continue
                if e.code in (429, 503, 408, 500, 502):
                    print(f"  HIT WALL {e.code}, sleep {wait:.0f}s ({detail})", flush=True)
                    time.sleep(wait)
                    wait = min(wait * 1.8, 600)
                    self._refresh(force=True)
                    continue
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"  network {e!r}, sleep {wait:.0f}s", flush=True)
                time.sleep(wait)
                wait = min(wait * 1.8, 600)
        raise RuntimeError("edge translate retries exhausted")


def pending_from_review(limit: int) -> list[tuple[Path, int, dict]]:
    items: list[tuple[Path, int, dict]] = []
    if not REVIEW_TITLES.is_dir():
        return items
    for path in sorted(REVIEW_TITLES.rglob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            row = parse_review_line(line)
            if not row:
                continue
            if row["status"] in PROTECTED:
                continue
            if row["zh"] and row["status"] == "machine":
                continue
            if not row["zh"] or row["status"] in ("pending", "error"):
                items.append((path, i, row))
                if len(items) >= limit:
                    return items
    return items


def write_review_updates(updates: dict[tuple[Path, int], dict]) -> None:
    by_path: dict[Path, dict[int, dict]] = {}
    for (path, idx), row in updates.items():
        by_path.setdefault(path, {})[idx] = row
    for path, idx_map in by_path.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, row in idx_map.items():
            if 0 <= idx < len(lines):
                lines[idx] = format_review_line(
                    row["id"], row["en"], row["zh"], row["status"]
                )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hourly Edge title translation (JSONL)")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--chunk", type=int, default=8)
    parser.add_argument("--owner", type=str, default="local")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    owner = f"{args.owner}-{int(time.time())}"
    try:
        acquire_lock(TRANSLATE_LOCK, owner)
    except LockError as e:
        print(f"ABORT: {e}", flush=True)
        return 2

    progress = read_json(
        TRANSLATE_PROGRESS,
        {
            "provider": "edge",
            "done_titles": 0,
            "failed": 0,
            "chars_sent": 0,
            "requests": 0,
            "last_error": None,
        },
    )

    try:
        pending = pending_from_review(args.batch)
        print(f"pending batch={len(pending)} (cap={args.batch})", flush=True)
        if not pending:
            progress["updated_at"] = now_iso()
            progress["last_batch"] = 0
            write_json(TRANSLATE_PROGRESS, progress)
            return 0

        if args.dry_run:
            for _, _, row in pending[:10]:
                print(f"  would translate {row['id']} {row['en'][:60]}")
            return 0

        tr = EdgeTranslator()
        updates: dict[tuple[Path, int], dict] = {}
        done = 0
        failed = 0
        chars = 0
        requests = 0

        for i in range(0, len(pending), args.chunk):
            wave = pending[i : i + args.chunk]
            texts = [w[2]["en"] for w in wave]
            try:
                zhs = tr.translate(texts)
                requests += 1
                chars += sum(len(t) for t in texts)
                for (path, idx, row), zh in zip(wave, zhs):
                    row = dict(row)
                    row["zh"] = zh
                    row["status"] = "machine"
                    updates[(path, idx)] = row
                    done += 1
                print(f"  ok {done}/{len(pending)}", flush=True)
            except Exception as e:  # noqa: BLE001
                failed += len(wave)
                progress["last_error"] = str(e)[:500]
                print(f"  FAIL wave: {e}", flush=True)
                for path, idx, row in wave:
                    row = dict(row)
                    row["status"] = "error"
                    updates[(path, idx)] = row
                break
            time.sleep(args.delay)

        write_review_updates(updates)

        progress["provider"] = "edge"
        progress["storage"] = "jsonl"
        progress["done_titles"] = int(progress.get("done_titles") or 0) + done
        progress["failed"] = int(progress.get("failed") or 0) + failed
        progress["chars_sent"] = int(progress.get("chars_sent") or 0) + chars
        progress["requests"] = int(progress.get("requests") or 0) + requests
        progress["updated_at"] = now_iso()
        progress["last_batch"] = done
        progress["last_failed"] = failed
        progress["pending_left_estimate"] = len(pending_from_review(10_000_000))
        write_json(TRANSLATE_PROGRESS, progress)
        print(json.dumps(progress, ensure_ascii=True, indent=2), flush=True)
        return 0 if failed == 0 else 1
    finally:
        release_lock(TRANSLATE_LOCK, owner)


if __name__ == "__main__":
    raise SystemExit(main())
