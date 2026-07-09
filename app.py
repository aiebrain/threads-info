"""Threads 인기글 수집기 - Flask 웹 서버."""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from scraper import get_output_path, run_scrape

app = Flask(__name__)
RESULTS_DIR = "./scraping-results"
LOG_DIR = "./logs"
VALID_SOURCE_MODES = {"hybrid", "threads_api", "apify"}
MAX_KEYWORDS = 10


def configure_logging() -> None:
    """Write operational diagnostics to ./logs/app.log without printing secrets."""
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(LOG_DIR) / "app.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path.resolve()) for h in root.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)


configure_logging()


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


def parse_recent_days(value) -> int | None:
    """Parse recent-date filter. None means all periods."""
    if value in (None, "", "all", "none", 0, "0"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("기간 필터는 전체, 3일, 7일, 30일 중 하나여야 합니다.")
    if parsed not in {3, 7, 30}:
        raise ValueError("기간 필터는 전체, 3일, 7일, 30일 중 하나여야 합니다.")
    return parsed


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """키워드로 Threads 인기글 후보를 수집한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON 요청 본문이 필요합니다."}), 400

    keywords = parse_keywords(data.get("keywords"))
    if not keywords:
        return jsonify({"error": "유효한 키워드가 없습니다."}), 400
    if len(keywords) > MAX_KEYWORDS:
        return jsonify({"error": f"키워드는 최대 {MAX_KEYWORDS}개까지 입력할 수 있습니다."}), 400

    try:
        max_results = parse_max_results(data.get("max_results", 20))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    korean_only = bool(data.get("korean_only", False))
    try:
        recent_days = parse_recent_days(data.get("recent_days", 7))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    source_mode = str(data.get("source_mode", "hybrid") or "hybrid")
    if source_mode not in VALID_SOURCE_MODES:
        return jsonify({"error": "source_mode는 hybrid, threads_api, apify 중 하나여야 합니다."}), 400

    try:
        app.logger.info(
            "scrape_request keywords=%s max_results=%s korean_only=%s recent_days=%s source_mode=%s",
            keywords,
            max_results,
            korean_only,
            recent_days,
            source_mode,
        )
        output = run_scrape(keywords, max_results, korean_only, source_mode, recent_days)
        app.logger.info(
            "scrape_result total_items=%s source_reports=%s",
            output.get("metadata", {}).get("total_items"),
            json.dumps(output.get("metadata", {}).get("source_reports", []), ensure_ascii=False),
        )
    except Exception:
        # Log server-side; return a generic message so internals aren't exposed.
        app.logger.exception("run_scrape failed")
        return jsonify({"error": "수집 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}), 500

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
    except (json.JSONDecodeError, OSError):
        app.logger.exception("failed to read result file")
        return jsonify({"error": "파일을 읽는 중 오류가 발생했습니다."}), 500


if __name__ == "__main__":
    os.makedirs(RESULTS_DIR, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(debug=debug, host=host, port=port)
