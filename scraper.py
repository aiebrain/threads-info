"""Threads 인기글 수집 스크립트

Apify의 themineworks/threads-scraper Actor를 사용해
키워드 검색 후 engagement 기준 인기 게시글을 수집한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from apify_client import ApifyClient

KST = timezone(timedelta(hours=9))

# 한글 유니코드 범위: 가-힣 (완성형), ㄱ-ㅎ, ㅏ-ㅣ (자모)
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3\u3131-\u3163\u1100-\u11FF]")


def contains_korean(text, min_ratio=0.05):
    """텍스트에 한국어가 포함되어 있는지 판별한다.
    min_ratio: 전체 문자 중 한글 비율 최소 기준 (기본 5%)
    """
    if not text:
        return False
    hangul_chars = HANGUL_RE.findall(text)
    # 공백/특수문자 제외한 실제 문자 수 기준
    alpha_chars = [c for c in text if c.isalnum()]
    if not alpha_chars:
        return False
    return len(hangul_chars) / len(alpha_chars) >= min_ratio


def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_output_path():
    now = datetime.now(KST).strftime("%Y%m%d_%H%M")
    output_dir = "./scraping-results"
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"threads_{now}.json")


def scrape_threads_apify(keyword, max_results=20, korean_only=False, apify_token=""):
    """Apify Actor를 사용해 Threads 검색 결과를 수집한다."""
    print(f"\n{'='*60}")
    print(f"키워드: {keyword}")
    print(f"Fetcher: Apify (themineworks/threads-scraper)")
    print(f"{'='*60}")

    if not apify_token:
        print("[ERROR] apify_token이 config.json에 설정되지 않았습니다.")
        print("[TIP] https://console.apify.com/settings/integrations 에서 토큰을 발급하세요.")
        return []

    client = ApifyClient(apify_token)

    run_input = {
        "mode": "search",
        "searchQuery": keyword,
        "maxPosts": max_results,
        "resultType": "top",
    }

    print(f"[INFO] Apify Actor 실행 중... (maxPosts={max_results})")

    try:
        run = client.actor("themineworks/threads-scraper").call(run_input=run_input)
    except Exception as e:
        print(f"[ERROR] Apify Actor 실행 실패: {e}")
        return []

    # 결과 수집
    items = list(client.dataset(run.default_dataset_id).iterate_items())
    print(f"[INFO] Apify에서 {len(items)}개 항목 수신")

    posts = []
    for item in items:
        text = item.get("text", "")
        if not text:
            continue

        username = item.get("username", "")
        like_count = int(item.get("like_count", 0) or 0)
        reply_count = int(item.get("reply_count", 0) or 0)
        repost_count = int(item.get("repost_count", 0) or 0)
        quote_count = int(item.get("quote_count", 0) or 0)
        engagement_total = like_count + reply_count + repost_count + quote_count

        # 타임스탬프
        timestamp = item.get("posted_at", "")

        # permalink
        permalink = item.get("url", "")

        # 미디어 URL
        media_urls = item.get("media_urls", [])
        media_url = media_urls[0] if media_urls else ""

        posts.append({
            "username": username,
            "text": text[:500],
            "like_count": like_count,
            "reply_count": reply_count,
            "repost_count": repost_count,
            "quote_count": quote_count,
            "engagement_total": engagement_total,
            "timestamp": timestamp,
            "permalink": permalink,
            "media_url": media_url,
        })

    # 한국어 필터링
    if korean_only:
        before = len(posts)
        posts = [p for p in posts if contains_korean(p["text"])]
        print(f"[INFO] 한국어 필터: {before}개 -> {len(posts)}개")

    # engagement 내림차순 정렬
    posts.sort(key=lambda x: x["engagement_total"], reverse=True)

    result = posts[:max_results]
    print(f"\n[결과] 총 {len(posts)}개 중 상위 {len(result)}개 선택")
    return result


def run_scrape(keywords, max_results=20, korean_only=False):
    """키워드 리스트로 Threads 인기글을 수집하고 결과 dict를 반환한다.

    Returns:
        dict: {"metadata": {...}, "data": [...]} 형태의 결과.
              수집 실패 시 data가 빈 리스트.
    """
    config = load_config()
    apify_token = config.get("apify_token", "")

    all_results = []

    for keyword in keywords:
        posts = scrape_threads_apify(keyword, max_results, korean_only, apify_token)
        if posts:
            all_results.extend(posts)

    # 전체 결과 다시 정렬
    all_results.sort(key=lambda x: x["engagement_total"], reverse=True)

    output = {
        "metadata": {
            "url": "https://www.threads.net/search",
            "keywords": keywords,
            "scraped_at": datetime.now(KST).isoformat(),
            "fetcher": "Apify (themineworks/threads-scraper)",
            "total_items": len(all_results),
        },
        "data": all_results,
    }

    return output


def main():
    config = load_config()
    keywords = config.get("keywords", ["AI"])
    max_results = config.get("max_results_per_keyword", 20)
    korean_only = config.get("korean_only", False)

    output = run_scrape(keywords, max_results, korean_only)

    if not output["data"]:
        print("\n[ERROR] 수집된 결과가 없습니다.")
        sys.exit(1)

    # 파일 저장
    output_path = get_output_path()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    all_results = output["data"]
    print(f"\n{'='*60}")
    print(f"저장 완료: {output_path}")
    print(f"수집 건수: {len(all_results)}건")
    print(f"{'='*60}")

    # 상위 5개 미리보기
    print("\n[상위 5개 미리보기]")
    for i, post in enumerate(all_results[:5], 1):
        text_preview = post["text"][:80].replace("\n", " ").encode("ascii", "replace").decode()
        username = post["username"].encode("ascii", "replace").decode()
        print(f"  {i}. @{username}: {text_preview}")
        print(f"     likes:{post['like_count']}  replies:{post['reply_count']}  reposts:{post['repost_count']}  total:{post['engagement_total']}")

    return output_path


if __name__ == "__main__":
    output_path = main()
