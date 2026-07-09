"""Threads 인기글 수집 스크립트

Threads(Meta)에서 키워드 검색 후 engagement 기준 인기 게시글을 수집한다.
DynamicFetcher로 JS 렌더링 후 data-sjs JSON에서 포스트 데이터를 파싱한다.
"""

import json
import os
import re
import sys
import time
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse

from nested_lookup import nested_lookup

KST = timezone(timedelta(hours=9))
SEARCH_URL = "https://www.threads.net/search?q={keyword}&serp_type=default"

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


def extract_posts_from_json(json_data):
    """data-sjs JSON에서 thread_items를 찾아 포스트 데이터를 추출한다."""
    posts = []

    # nested_lookup으로 thread_items 키를 재귀 탐색
    thread_items_list = nested_lookup("thread_items", json_data)
    if not thread_items_list:
        # 다른 가능한 키 이름도 시도
        thread_items_list = nested_lookup("items", json_data)

    for thread_items in thread_items_list:
        if not isinstance(thread_items, list):
            continue
        for item in thread_items:
            post = extract_single_post(item)
            if post:
                posts.append(post)

    return posts


def extract_single_post(item):
    """개별 thread_item에서 포스트 정보를 추출한다."""
    try:
        # thread_items 내부 구조: item > post > ...
        post_data = item
        if "post" in item:
            post_data = item["post"]
        elif "thread_items" in item:
            return None

        # 본문 텍스트
        text = ""
        caption = post_data.get("caption")
        if caption:
            if isinstance(caption, dict):
                text = caption.get("text", "")
            elif isinstance(caption, str):
                text = caption

        if not text:
            text_candidates = nested_lookup("text", post_data)
            for t in text_candidates:
                if isinstance(t, str) and len(t) > 10:
                    text = t
                    break

        if not text:
            return None

        # 작성자
        username = ""
        user = post_data.get("user") or post_data.get("owner") or {}
        if isinstance(user, dict):
            username = user.get("username", "")
        if not username:
            usernames = nested_lookup("username", post_data)
            for u in usernames:
                if isinstance(u, str) and u:
                    username = u
                    break

        # engagement 수치
        like_count = _get_count(post_data, ["like_count", "likes", "like_and_view_counts_disabled"])
        reply_count = _get_count(post_data, ["reply_count", "text_post_app_reply_count", "direct_reply_count"])
        repost_count = _get_count(post_data, ["repost_count", "reshare_count", "text_post_app_share_count"])
        quote_count = _get_count(post_data, ["quote_count"])

        # like_and_view_counts_disabled가 True이면 like_count가 0일 수 있음
        engagement_total = like_count + reply_count + repost_count + quote_count

        # 타임스탬프
        timestamp = ""
        taken_at = post_data.get("taken_at") or post_data.get("device_timestamp")
        if taken_at:
            try:
                taken_at_int = int(taken_at)
                if taken_at_int > 1e12:
                    taken_at_int = taken_at_int // 1000
                timestamp = datetime.fromtimestamp(taken_at_int, tz=KST).isoformat()
            except (ValueError, OSError):
                timestamp = str(taken_at)

        # permalink
        code = post_data.get("code", "")
        permalink = f"https://www.threads.net/@{username}/post/{code}" if code else ""
        if not code:
            pk = post_data.get("pk", "") or post_data.get("id", "")
            if pk:
                permalink = f"https://www.threads.net/post/{pk}"

        # 미디어 URL
        media_url = ""
        image_versions = post_data.get("image_versions2") or {}
        candidates = image_versions.get("candidates", [])
        if candidates and isinstance(candidates, list):
            media_url = candidates[0].get("url", "")
        if not media_url:
            urls = nested_lookup("url", post_data)
            for u in urls:
                if isinstance(u, str) and ("scontent" in u or "cdninstagram" in u):
                    media_url = u
                    break

        return {
            "username": username,
            "text": text[:500],  # 너무 긴 텍스트 제한
            "like_count": like_count,
            "reply_count": reply_count,
            "repost_count": repost_count,
            "quote_count": quote_count,
            "engagement_total": engagement_total,
            "timestamp": timestamp,
            "permalink": permalink,
            "media_url": media_url,
        }
    except Exception as e:
        print(f"  [WARN] 포스트 파싱 실패: {e}")
        return None


def _get_count(data, keys):
    """여러 가능한 키 이름으로 수치를 추출한다."""
    for key in keys:
        val = data.get(key)
        if val is not None:
            if isinstance(val, bool):
                continue
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    # nested_lookup fallback
    for key in keys:
        vals = nested_lookup(key, data)
        for v in vals:
            if isinstance(v, bool):
                continue
            try:
                return int(v)
            except (ValueError, TypeError):
                continue
    return 0


def scrape_threads(keyword, max_results=20, use_stealthy=False, korean_only=False):
    """Threads 검색 결과를 스크래핑한다."""
    url = SEARCH_URL.format(keyword=quote(keyword))
    print(f"\n{'='*60}")
    print(f"키워드: {keyword}")
    print(f"URL: {url}")
    print(f"Fetcher: {'StealthyFetcher' if use_stealthy else 'DynamicFetcher'}")
    print(f"{'='*60}")

    def scroll_and_wait(page):
        """검색 결과 로딩을 위해 스크롤 후 대기"""
        page.wait_for_timeout(3000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

    if use_stealthy:
        from scrapling.fetchers import StealthyFetcher
        print("[INFO] StealthyFetcher로 접속 중...")
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            timeout=60000,
            wait=5000,
            page_action=scroll_and_wait,
        )
    else:
        from scrapling.fetchers import DynamicFetcher
        print("[INFO] DynamicFetcher로 접속 중...")
        page = DynamicFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            timeout=60000,
            wait=5000,
            page_action=scroll_and_wait,
        )

    # 차단 감지
    page_text = page.css("body::text").get() or ""
    page_html = page.html_content if hasattr(page, "html_content") else ""

    if "로그인" in page_text and len(page_html) < 5000:
        print("[WARN] 로그인 요구 감지됨")
        if not use_stealthy:
            print("[INFO] StealthyFetcher로 재시도...")
            return scrape_threads(keyword, max_results, use_stealthy=True, korean_only=korean_only)
        else:
            print("[ERROR] StealthyFetcher로도 차단됨. 수집 불가.")
            return []

    # data-sjs script 태그에서 JSON 추출
    print("[INFO] data-sjs JSON 파싱 중...")
    scripts = page.css('script[type="application/json"][data-sjs]')
    print(f"  발견된 data-sjs script 태그: {len(scripts)}개")

    all_posts = []

    for i, script in enumerate(scripts):
        raw = script.css("::text").get()
        if not raw:
            continue
        try:
            json_data = json.loads(raw)
            posts = extract_posts_from_json(json_data)
            if posts:
                print(f"  script[{i}]: {len(posts)}개 포스트 발견")
                all_posts.extend(posts)
        except json.JSONDecodeError:
            continue

    # data-sjs에서 못 찾으면 다른 script 태그도 시도
    if not all_posts:
        print("[INFO] data-sjs에서 포스트 미발견. 다른 script 태그 탐색...")
        all_scripts = page.css('script[type="application/json"]')
        print(f"  전체 application/json script 태그: {len(all_scripts)}개")

        for i, script in enumerate(all_scripts):
            raw = script.css("::text").get()
            if not raw or len(raw) < 100:
                continue
            try:
                json_data = json.loads(raw)
                posts = extract_posts_from_json(json_data)
                if posts:
                    print(f"  script[{i}]: {len(posts)}개 포스트 발견")
                    all_posts.extend(posts)
            except json.JSONDecodeError:
                continue

    # 전체 페이지 HTML에서 JSON 블록 추출 시도 (최후 수단)
    if not all_posts:
        print("[INFO] script 태그 파싱 실패. HTML 내 JSON 직접 탐색...")
        json_pattern = re.compile(r'\{["\']thread_items["\']\s*:\s*\[', re.DOTALL)
        matches = json_pattern.finditer(page_html)
        for match in matches:
            start = match.start()
            # 균형 잡힌 중괄호로 JSON 종료 지점 찾기
            depth = 0
            end = start
            for j in range(start, min(start + 100000, len(page_html))):
                if page_html[j] == '{':
                    depth += 1
                elif page_html[j] == '}':
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            if end > start:
                try:
                    json_data = json.loads(page_html[start:end])
                    posts = extract_posts_from_json(json_data)
                    if posts:
                        print(f"  HTML 내 JSON에서 {len(posts)}개 포스트 발견")
                        all_posts.extend(posts)
                except json.JSONDecodeError:
                    continue

    if not all_posts:
        print("[WARN] 포스트를 찾지 못했습니다.")
        if not use_stealthy:
            print("[INFO] StealthyFetcher로 재시도...")
            return scrape_threads(keyword, max_results, use_stealthy=True, korean_only=korean_only)
        else:
            print("[ERROR] 포스트를 추출할 수 없습니다.")
            print("[TIP] Threads 내부 JSON 구조가 변경되었을 수 있습니다.")
            print("[TIP] Apify의 Threads Scraper를 대안으로 고려하세요:")
            print("       https://apify.com/apify/threads-scraper")

            # 디버그용: 페이지 HTML 일부 저장
            debug_path = "./scraping-results/debug_page.html"
            os.makedirs("./scraping-results", exist_ok=True)
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(page_html[:200000] if page_html else "empty")
            print(f"[DEBUG] 페이지 HTML 저장됨: {debug_path}")
            return []

    # 중복 제거 (permalink 기준)
    seen = set()
    unique_posts = []
    for post in all_posts:
        key = post["permalink"] or post["text"][:100]
        if key not in seen:
            seen.add(key)
            unique_posts.append(post)

    # 한국어 필터링
    if korean_only:
        before = len(unique_posts)
        unique_posts = [p for p in unique_posts if contains_korean(p["text"])]
        print(f"[INFO] 한국어 필터: {before}개 -> {len(unique_posts)}개")

    # engagement 내림차순 정렬
    unique_posts.sort(key=lambda x: x["engagement_total"], reverse=True)

    # 상위 N개만
    result = unique_posts[:max_results]
    print(f"\n[결과] 총 {len(unique_posts)}개 중 상위 {len(result)}개 선택")
    return result


def run_scrape(keywords, max_results=20, korean_only=False):
    """키워드 리스트로 Threads 인기글을 수집하고 결과 dict를 반환한다.

    Returns:
        dict: {"metadata": {...}, "data": [...]} 형태의 결과.
              수집 실패 시 data가 빈 리스트.
    """
    delay_range = [2, 3]
    all_results = []
    fetcher_used = "DynamicFetcher"

    for i, keyword in enumerate(keywords):
        posts = scrape_threads(keyword, max_results, korean_only=korean_only)
        # 공백 포함 키워드가 결과 없으면 붙여쓰기로 재시도
        if not posts and " " in keyword:
            merged = keyword.replace(" ", "")
            print(f"\n[INFO] '{keyword}' 결과 없음. '{merged}'로 재시도...")
            posts = scrape_threads(merged, max_results, korean_only=korean_only)
        if posts:
            all_results.extend(posts)
            fetcher_used = "DynamicFetcher"

        # 키워드 간 딜레이
        if i < len(keywords) - 1:
            delay = random.uniform(delay_range[0], delay_range[1])
            print(f"\n[INFO] 다음 키워드까지 {delay:.1f}초 대기...")
            time.sleep(delay)

    # 전체 결과 다시 정렬
    all_results.sort(key=lambda x: x["engagement_total"], reverse=True)

    output = {
        "metadata": {
            "url": "https://www.threads.net/search",
            "keywords": keywords,
            "scraped_at": datetime.now(KST).isoformat(),
            "fetcher": fetcher_used,
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
