import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from app_flow import TRASH_THRESHOLD, analyze_prompt_flow, improve_prompt_flow
from logging_config import setup_logger, truncate
from prompt_log import add_log_entry, get_logs

load_dotenv()

app = Flask(__name__)
logger = setup_logger("main")


def _result_scores(result: dict) -> tuple[float, float, float]:
    return (
        float(result.get("final_score", 0) or 0),
        float(result.get("bert_score", 0) or 0),
        float(result.get("llm_score", 0) or 0),
    )


@app.route("/")
def home():
    return render_template("index.html", trash_threshold=TRASH_THRESHOLD)


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()

    if not prompt:
        return jsonify({"error": "Please enter a prompt."}), 400

    start = time.perf_counter()
    try:
        result = analyze_prompt_flow(prompt)
        final_score, bert_score, llm_score = _result_scores(result)
        status = result.get("status", "ERROR")
        elapsed = (time.perf_counter() - start) * 1000

        add_log_entry(
            prompt,
            "ANALYZED",
            status,
            final_score,
            bert_score=bert_score,
            llm_score=llm_score if status == "ACCEPTED" else 0,
        )

        logger.info(
            "POST /analyze | status=%s score=%.2f | %.0fms | '%s'",
            status,
            final_score,
            elapsed,
            truncate(prompt),
        )

        if status == "REJECTED":
            return jsonify(
                {
                    "success": True,
                    "final_score": final_score,
                    "bert_score": bert_score,
                    "llm_score": 0,
                    "status": "REJECTED",
                    "msg": result.get("msg", "Prompt is too vague or low quality."),
                    "threshold": TRASH_THRESHOLD,
                }
            )

        if status == "ERROR":
            return jsonify({"error": result.get("msg", "Analysis failed.")}), 500

        return jsonify(
            {
                "success": True,
                "final_score": final_score,
                "bert_score": bert_score,
                "llm_score": llm_score,
                "status": "ACCEPTED",
                "threshold": TRASH_THRESHOLD,
            }
        )

    except Exception as exc:
        logger.error("POST /analyze failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/improve", methods=["POST"])
def improve():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()

    if not prompt:
        return jsonify({"error": "Please enter a prompt."}), 400

    start = time.perf_counter()
    try:
        result = improve_prompt_flow(
            prompt,
            known_score=data.get("known_score"),
            known_status=data.get("known_status"),
            known_bert=data.get("known_bert"),
            known_llm=data.get("known_llm"),
        )
        final_score, bert_score, llm_score = _result_scores(result)
        status = result.get("status", "ERROR")
        elapsed = (time.perf_counter() - start) * 1000

        add_log_entry(
            prompt,
            "IMPROVED",
            status,
            final_score,
            bert_score=bert_score,
            llm_score=llm_score,
            iterations=result.get("iterations"),
        )

        logger.info(
            "POST /improve | status=%s score=%.2f iters=%s | %.0fms | '%s'",
            status,
            final_score,
            result.get("iterations"),
            elapsed,
            truncate(prompt),
        )

        if status == "ERROR":
            return jsonify({"error": result.get("msg", "Improvement failed.")}), 500

        return jsonify(
            {
                "success": True,
                "status": status,
                "original": result.get("original"),
                "improved": result.get("improved"),
                "original_score": result.get("original_score"),
                "final_score": final_score,
                "bert_score": bert_score,
                "llm_score": llm_score,
                "iterations": result.get("iterations", 0),
                "msg": result.get("msg"),
                "threshold": TRASH_THRESHOLD,
                "analysis_status": result.get("analysis_status"),
            }
        )

    except Exception as exc:
        logger.error("POST /improve failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/logs", methods=["GET"])
def logs():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"logs": get_logs(limit=min(limit, 50))})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
