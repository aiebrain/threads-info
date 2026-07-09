"""Threads 인기글 하이브리드 수집 로직.

수집 전략:
1) 공식 Threads API keyword_search TOP으로 후보 ID를 확보한다.
2) Apify Actor가 설정되어 있으면 원문/링크/반응수치를 보강한다.
3) 공식 API가 개발/미검수 앱 제한으로 public post 전체 필드를 막으면
   ID-only 후보로 저장하고 브라우저/공개 페이지 검증 대상으로 표시한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from apify_client import ApifyClient

KST = timezone(timedelta(hours=9))
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3\u3131-\u3163\u1100-\u11FF]")
THREADS_BASE = "https://graph.threads.net/v1.0"
THREADS_FIELDS = "id,text,media_type,permalink,timestamp,username,has_replies,is_quote_post,is_reply"
DEFAULT_RESULTS_DIR = "./scraping-results"


def contains_korean(text: str, min_ratio: float = 0.05) -> bool:
    """텍스트에 한국어가 포함되어 있는지 판별한다."""
    if not text:
        return False
    hangul_chars = HANGUL_RE.findall(text)
    alpha_chars = [c for c in text if c.isalnum()]
    if not alpha_chars:
        return False
    return len(hangul_chars) / len(alpha_chars) >= min_ratio


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE lines into os.environ without overriding existing env."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_environment() -> None:
    load_dotenv(Path(".env"))
    load_dotenv(Path.home() / ".hermes" / ".env")


def load_config(path: str = "config.json") -> dict[str, Any]:
    """Load optional config.json. Environment variables are also supported."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_output_path(results_dir: str = DEFAULT_RESULTS_DIR) -> str:
    now = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(results_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / f"threads_{now}.json")


def get_setting(config: dict[str, Any], key: str, env_keys: list[str], default: Any = "") -> Any:
    for env_key in env_keys:
        value = os.environ.get(env_key)
        if value:
            return value
    return config.get(key, default)


def http_get_json(url: str) -> tuple[bool, dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": "threads-info/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return True, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body[:500]}
        data["_status"] = e.code
        return False, data
    except Exception as e:
        return False, {"error": str(e)}


def normalize_api_row(row: dict[str, Any], keyword: str, mode: str) -> dict[str, Any]:
    text = row.get("text") or ""
    post_id = row.get("id", "")
    permalink = row.get("permalink") or ""
    notes = "Official Threads API candidate. Engagement metrics require public-page/browser verification."
    if not text:
        notes = f"Official Threads API returned ranked candidate ID only: {post_id}. Verify text/link/metrics via browser or Apify."
    return {
        "source": mode,
        "keyword": keyword,
        "id": post_id,
        "username": row.get("username", ""),
        "text": text,
        "like_count": 0,
        "reply_count": 0,
        "repost_count": 0,
        "quote_count": 0,
        "engagement_total": 0,
        "timestamp": row.get("timestamp", ""),
        "permalink": permalink,
        "media_url": "",
        "verification_status": "api_collected_metric_pending" if text else "api_id_only_browser_verification_required",
        "notes": notes,
    }


def scrape_threads_official_api(keyword: str, max_results: int = 20, token: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use official Threads keyword_search as candidate discovery.

    In dev/unreviewed mode, full public fields may fail even though TOP IDs work.
    This function falls back to ID-only candidate rows.
    """
    meta: dict[str, Any] = {"source": "threads_api", "keyword": keyword, "status": "skipped"}
    if not token:
        meta.update({"status": "skipped", "reason": "THREADS_ACCESS_TOKEN missing"})
        return [], meta

    limit = str(min(max(max_results, 1), 100))
    full_params = {
        "q": keyword,
        "search_type": "TOP",
        "search_mode": "KEYWORD",
        "limit": limit,
        "fields": THREADS_FIELDS,
        "access_token": token,
    }
    url = THREADS_BASE + "/keyword_search?" + urllib.parse.urlencode(full_params)
    ok, data = http_get_json(url)
    if ok:
        rows = [normalize_api_row(r, keyword, "threads_api_full") for r in data.get("data", [])]
        meta.update({"status": "ok", "mode": "full", "count": len(rows)})
        return rows, meta

    # Fallback: ranked IDs only.
    id_params = {
        "q": keyword,
        "search_type": "TOP",
        "search_mode": "KEYWORD",
        "limit": limit,
        "access_token": token,
    }
    url = THREADS_BASE + "/keyword_search?" + urllib.parse.urlencode(id_params)
    ok2, data2 = http_get_json(url)
    if ok2:
        rows = [normalize_api_row(r, keyword, "threads_api_id_only") for r in data2.get("data", [])]
        meta.update({
            "status": "partial",
            "mode": "id_only",
            "count": len(rows),
            "full_fields_error": data.get("error", data),
        })
        return rows, meta

    meta.update({"status": "error", "error": data2.get("error", data2), "full_fields_error": data.get("error", data)})
    return [], meta


def scrape_threads_apify(keyword: str, max_results: int = 20, korean_only: bool = False, apify_token: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apify Actor를 사용해 Threads 검색 결과 원문/반응수치를 수집한다."""
    meta: dict[str, Any] = {"source": "apify", "keyword": keyword, "status": "skipped"}
    if not apify_token:
        meta.update({"status": "skipped", "reason": "APIFY_TOKEN/apify_token missing"})
        return [], meta

    client = ApifyClient(apify_token)
    run_input = {
        "mode": "search",
        "searchQuery": keyword,
        "maxPosts": max_results,
        "resultType": "top",
    }

    try:
        run = client.actor("themineworks/threads-scraper").call(run_input=run_input)
        items = list(client.dataset(run.default_dataset_id).iterate_items())
    except Exception as e:
        meta.update({"status": "error", "error": str(e)})
        return [], meta

    posts: list[dict[str, Any]] = []
    for item in items:
        text = item.get("text", "") or ""
        if not text:
            continue

        like_count = int(item.get("like_count", 0) or 0)
        reply_count = int(item.get("reply_count", 0) or 0)
        repost_count = int(item.get("repost_count", 0) or 0)
        quote_count = int(item.get("quote_count", 0) or 0)
        engagement_total = like_count + reply_count + repost_count + quote_count
        media_urls = item.get("media_urls", []) or []

        posts.append({
            "source": "apify",
            "keyword": keyword,
            "id": str(item.get("id", "") or item.get("pk", "")),
            "username": item.get("username", ""),
            "text": text[:1000],
            "like_count": like_count,
            "reply_count": reply_count,
            "repost_count": repost_count,
            "quote_count": quote_count,
            "engagement_total": engagement_total,
            "timestamp": item.get("posted_at", ""),
            "permalink": item.get("url", ""),
            "media_url": media_urls[0] if media_urls else "",
            "verification_status": "apify_collected_public_page_verification_recommended",
            "notes": "Apify collected text/link/engagement. Verify top rows in browser before publishing insights.",
        })

    if korean_only:
        posts = [p for p in posts if contains_korean(p["text"])]

    posts.sort(key=lambda x: x["engagement_total"], reverse=True)
    result = posts[:max_results]
    meta.update({"status": "ok", "received": len(items), "count": len(result), "korean_only": korean_only})
    return result, meta


def dedupe_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer Apify/full rows over ID-only duplicates when ids or permalinks match."""
    priority = {"apify": 3, "threads_api_full": 2, "threads_api_id_only": 1}
    best: dict[str, dict[str, Any]] = {}
    no_key: list[dict[str, Any]] = []
    for row in rows:
        key = row.get("permalink") or row.get("id")
        if not key:
            no_key.append(row)
            continue
        prev = best.get(key)
        if not prev or priority.get(row.get("source", ""), 0) > priority.get(prev.get("source", ""), 0):
            best[key] = row
    merged = list(best.values()) + no_key
    merged.sort(key=lambda x: (x.get("engagement_total", 0), 1 if x.get("text") else 0), reverse=True)
    return merged


def run_scrape(keywords: list[str], max_results: int = 20, korean_only: bool = False, source_mode: str = "hybrid") -> dict[str, Any]:
    """키워드 리스트로 Threads 인기글 후보를 수집하고 결과 dict를 반환한다."""
    load_environment()
    config = load_config()
    apify_token = get_setting(config, "apify_token", ["APIFY_TOKEN", "APIFY_API_TOKEN"], "")
    threads_token = get_setting(config, "threads_access_token", ["THREADS_ACCESS_TOKEN"], "")

    all_results: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []

    for keyword in keywords:
        if source_mode in {"hybrid", "threads_api"}:
            api_rows, api_meta = scrape_threads_official_api(keyword, max_results, threads_token)
            source_reports.append(api_meta)
            all_results.extend(api_rows)
        if source_mode in {"hybrid", "apify"}:
            apify_rows, apify_meta = scrape_threads_apify(keyword, max_results, korean_only, apify_token)
            source_reports.append(apify_meta)
            all_results.extend(apify_rows)

    all_results = dedupe_results(all_results)

    output = {
        "metadata": {
            "url": "https://www.threads.net/search",
            "keywords": keywords,
            "scraped_at": datetime.now(KST).isoformat(),
            "fetcher": "hybrid: official Threads API candidates + Apify enrichment",
            "source_mode": source_mode,
            "total_items": len(all_results),
            "source_reports": source_reports,
        },
        "data": all_results,
    }
    return output


def main() -> str:
    load_environment()
    config = load_config()
    keywords = config.get("keywords", ["AI"])
    max_results = int(config.get("max_results_per_keyword", 20))
    korean_only = bool(config.get("korean_only", False))
    source_mode = config.get("source_mode", "hybrid")

    output = run_scrape(keywords, max_results, korean_only, source_mode)
    if not output["data"]:
        print("\n[ERROR] 수집된 결과가 없습니다.")
        sys.exit(1)

    output_path = get_output_path()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"저장 완료: {output_path}")
    print(f"수집 건수: {len(output['data'])}건")
    for i, post in enumerate(output["data"][:5], 1):
        text_preview = (post.get("text") or post.get("notes", ""))[:80].replace("\n", " ")
        print(f"{i}. [{post.get('source')}] @{post.get('username', '')}: {text_preview}")
    return output_path


if __name__ == "__main__":
    main()
