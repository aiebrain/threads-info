"""Threads 인기글 수집기 - Flask 웹 서버

브라우저에서 키워드 입력 -> 수집 실행 -> 결과 테이블 표시를 수행한다.
"""

import json
import os
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, jsonify

from scraper import run_scrape, get_output_path

KST = timezone(timedelta(hours=9))

app = Flask(__name__)

RESULTS_DIR = "./scraping-results"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """키워드로 Threads 인기글을 수집한다."""
    data = request.get_json()
    if not data or not data.get("keywords"):
        return jsonify({"error": "키워드를 입력해주세요."}), 400

    keywords_raw = data["keywords"]
    # 쉼표 또는 줄바꿈으로 구분된 키워드 파싱
    if isinstance(keywords_raw, str):
        keywords = [k.strip() for k in keywords_raw.replace("\n", ",").split(",") if k.strip()]
    else:
        keywords = keywords_raw

    if not keywords:
        return jsonify({"error": "유효한 키워드가 없습니다."}), 400

    max_results = int(data.get("max_results", 20))
    korean_only = bool(data.get("korean_only", False))

    try:
        output = run_scrape(keywords, max_results, korean_only)
    except Exception as e:
        return jsonify({"error": f"수집 중 오류 발생: {str(e)}"}), 500

    # 결과 파일 저장
    if output["data"]:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        output_path = get_output_path()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        output["metadata"]["saved_file"] = os.path.basename(output_path)

    return jsonify(output)


@app.route("/api/history")
def api_history():
    """저장된 결과 파일 목록을 반환한다."""
    if not os.path.isdir(RESULTS_DIR):
        return jsonify([])

    files = []
    for fname in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if not fname.endswith(".json") or fname == "debug_page.html":
            continue
        fpath = os.path.join(RESULTS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("metadata", {})
            files.append({
                "filename": fname,
                "keywords": meta.get("keywords", []),
                "scraped_at": meta.get("scraped_at", ""),
                "total_items": meta.get("total_items", 0),
            })
        except (json.JSONDecodeError, OSError):
            files.append({
                "filename": fname,
                "keywords": [],
                "scraped_at": "",
                "total_items": 0,
            })

    return jsonify(files)


@app.route("/api/result/<filename>")
def api_result(filename):
    """특정 결과 파일의 내용을 반환한다."""
    # 경로 탈출 방지
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "잘못된 파일명"}), 400

    fpath = os.path.join(RESULTS_DIR, filename)
    if not os.path.isfile(fpath):
        return jsonify({"error": "파일을 찾을 수 없습니다."}), 404

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({"error": f"파일 읽기 오류: {str(e)}"}), 500


if __name__ == "__main__":
    os.makedirs(RESULTS_DIR, exist_ok=True)
    app.run(debug=True, host="0.0.0.0", port=5000)
