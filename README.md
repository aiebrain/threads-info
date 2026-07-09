# Threads 인기글 후보 수집기

공식 Threads API와 Apify를 조합해 키워드별 Threads 인기글 후보를 수집하는 CLI + Flask 웹 UI입니다.

## 수집 방식

- `threads_api`: 공식 Threads API `keyword_search TOP`으로 후보를 찾습니다.
  - 장점: 공식 API 기반이라 후보 랭킹 신뢰도가 높습니다.
  - 제한: 개발/미검수 앱에서는 public post 전체 원문/퍼머링크/수치 필드가 제한될 수 있습니다. 이 경우 ID-only 후보로 저장합니다.
- `apify`: Apify Actor `themineworks/threads-scraper`로 원문/링크/좋아요/댓글/리포스트 수치를 보강합니다.
  - 장점: 콘텐츠 분석에 필요한 본문과 반응수치를 바로 얻기 쉽습니다.
  - 제한: 외부 Actor 의존, 비용/필드 변경 가능성이 있습니다.
- `hybrid`: 추천 모드입니다. 공식 API 후보 + Apify 보강 결과를 합쳐 저장합니다.

## 설치

```bash
git clone https://github.com/aiebrain/threads-info.git
cd threads-info
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 설정

민감값은 git에 올리지 마세요.

```bash
cp .env.example .env
cp config.example.json config.json
```

`.env` 예시:

```bash
THREADS_ACCESS_TOKEN=...
APIFY_TOKEN=...
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=0
```

`config.json` 예시:

```json
{
  "source_mode": "hybrid",
  "keywords": ["AI", "자동화"],
  "max_results_per_keyword": 20,
  "korean_only": true,
  "apify_token": ""
}
```

우선순위:

1. OS 환경변수
2. `.env`
3. `~/.hermes/.env`
4. `config.json`

## 웹 UI 실행

```bash
python app.py
```

브라우저에서 엽니다.

```text
http://127.0.0.1:5000
```

## CLI 실행

```bash
python scraper.py
```

결과는 `scraping-results/threads_YYYYMMDD_HHMMSS.json`에 저장됩니다.

## API 사용 예시

```bash
curl -s -X POST http://127.0.0.1:5000/api/scrape \
  -H 'Content-Type: application/json' \
  -d '{"keywords":"AI 자동화, 상세페이지", "max_results": 10, "korean_only": true, "source_mode":"hybrid"}'
```

## 결과 필드

각 row는 대략 아래 필드를 포함합니다.

- `source`: `threads_api_full`, `threads_api_id_only`, `apify`
- `keyword`
- `id`
- `username`
- `text`
- `like_count`, `reply_count`, `repost_count`, `quote_count`, `engagement_total`
- `timestamp`
- `permalink`
- `verification_status`
- `notes`

## 운영 메모

- 발행/강의/마케팅 인사이트로 사용하기 전에는 상위 후보를 브라우저에서 재검증하세요.
- 공식 API가 ID-only만 반환하는 경우, 원문/링크/반응수치는 Apify 또는 브라우저 검증 단계에서 보강해야 합니다.
- `.env`, `config.json`, `scraping-results/`는 `.gitignore`에 포함되어 있습니다.
