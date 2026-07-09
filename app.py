"""Threads 인기글 수집기 - Flask 웹 서버."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from scraper import get_output_path, run_scrape

app = Flask(__name__)
RESULTS_DIR = "./scraping-results"
VALID_SOURCE_MODES = {"hybrid", "threads_api", "apify"}


@app.route("/")
def index():
    return render_template("index.html")


def parse_keywords(value) -> list[str]:
    if isinstance(value, str):
        return [k.strip() for k in value.replace("\n", ",").split(",") if k.strip()]
    if isinstance(value, list):
        return [str(k).strip() for k in value if str(k).strip()]
    return []


def parse_max_results(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("최대 건수는 숫자로 입력해주세요.")
    if parsed < 1:
        raise ValueError("최대 건수는 1 이상이어야 합니다.")
    return min(parsed, 100)


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """키워드로 Threads 인기글 후보를 수집한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON 요청 본문이 필요합니다."}), 400

    keywords = parse_keywords(data.get("keywords"))
    if not keywords:
        return jsonify({"error": "유효한 키워드가 없습니다."}), 400

    try:
        max_results = parse_max_results(data.get("max_results", 20))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    korean_only = bool(data.get("korean_only", False))
    source_mode = str(data.get("source_mode", "hybrid") or "hybrid")
    if source_mode not in VALID_SOURCE_MODES:
        return jsonify({"error": "source_mode는 hybrid, threads_api, apify 중 하나여야 합니다."}), 400

    try:
        output = run_scrape(keywords, max_results, korean_only, source_mode)
    except Exception as e:
        return jsonify({"error": f"수집 중 오류 발생: {str(e)}"}), 500

    if output["data"]:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        output_path = get_output_path(RESULTS_DIR)
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
                "source_mode": meta.get("source_mode", ""),
            })
        except (json.JSONDecodeError, OSError):
            files.append({
                "filename": fname,
                "keywords": [],
                "scraped_at": "",
                "total_items": 0,
                "source_mode": "",
            })

    return jsonify(files)


@app.route("/api/result/<filename>")
def api_result(filename):
    """특정 결과 파일의 내용을 반환한다."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "잘못된 파일명"}), 400

    fpath = Path(RESULTS_DIR) / filename
    if not fpath.is_file():
        return jsonify({"error": "파일을 찾을 수 없습니다."}), 404

    try:
        with fpath.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({"error": f"파일 읽기 오류: {str(e)}"}), 500


if __name__ == "__main__":
    os.makedirs(RESULTS_DIR, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(debug=debug, host=host, port=port)
