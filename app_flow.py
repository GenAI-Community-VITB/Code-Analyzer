import os
import re
import time
from typing import Any

import requests
from dotenv import load_dotenv
from langdetect import LangDetectException, detect

from logging_config import setup_logger, truncate

load_dotenv()

logger = setup_logger("app_flow")

# --- CONFIGURATION ---
BERT_API_URL = os.environ.get("BERT_API_URL")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
MISTRAL_API_URL = os.environ.get(
    "MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions"
)
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

TRASH_THRESHOLD = float(os.environ.get("TRASH_THRESHOLD", "80"))
MAX_IMPROVE_ITERS = int(os.environ.get("MAX_IMPROVE_ITERS", "3"))
TARGET_SCORE = float(os.environ.get("TARGET_SCORE", "90"))

WEIGHT_BERT = 30
WEIGHT_LLM = 70
TOTAL_WEIGHT = WEIGHT_BERT + WEIGHT_LLM

if not BERT_API_URL:
    logger.warning("BERT_API_URL is not set — BERT scoring will fail until configured.")
if not MISTRAL_API_KEY:
    logger.warning("MISTRAL_API_KEY is not set — LLM scoring/improve will fail.")


# --- 1. HEURISTIC FILTERS ---
def is_valid_prompt(text: str) -> dict:
    if len(text.split()) < 3:
        return {"valid": False, "reason": "Prompt is too short."}

    words = text.lower().split()
    if len(words) > 0:
        most_common = max(set(words), key=words.count)
        if words.count(most_common) / len(words) > 0.5:
            return {"valid": False, "reason": "Detected repetitive spam."}

    try:
        lang = detect(text)
        if lang != "en":
            return {"valid": False, "reason": f"Detected non-English text ({lang})."}
    except LangDetectException:
        pass

    return {"valid": True, "reason": "Pass"}


def _bert_score_raw(text: str) -> float:
    if not BERT_API_URL:
        return 0.0
    try:
        response = requests.post(BERT_API_URL, json={"prompt": text}, timeout=30)
        if response.status_code == 200:
            return float(response.json().get("score", 0.0))
        logger.warning("BERT API returned status %s", response.status_code)
        return 0.0
    except requests.RequestException as exc:
        logger.error("BERT API request failed: %s", exc)
        return 0.0


def _mistral_chat(
    *,
    system_message: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 512,
    retries: int = 3,
) -> str:
    if not MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY is not configured.")

    payload = {
        "model": MISTRAL_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                MISTRAL_API_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
            if response.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 8)
                logger.warning(
                    "Mistral HTTP %s (attempt %d/%d) — retrying in %ds",
                    response.status_code,
                    attempt,
                    retries,
                    wait,
                )
                time.sleep(wait)
                last_error = requests.HTTPError(
                    f"{response.status_code} Server Error for url: {MISTRAL_API_URL}",
                    response=response,
                )
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                wait = min(2 ** attempt, 8)
                logger.warning(
                    "Mistral request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt,
                    retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
            else:
                break

    raise RuntimeError(f"Mistral API unavailable after {retries} attempts: {last_error}")


SCORING_SYSTEM = """You are a Master Prompt Engineer. Rate prompts on 7 metrics (0-100).

SCORING GUIDELINES:
1. Intent Strength:
   - 100 = Explicit instruction ("Act as...", "Write a...", "Fix code...", "Imagine...").
   - 0 = Pure content (Poem, Story, Statement) with NO request.
2. Clarity: Is the goal unambiguous?
3. Specificity: detailed vs vague?
4. Context: Is background provided?
5. Constraints: Are limits defined?
6. Complexity: Does it require reasoning?
7. Role Definition: Does it assign a persona? (e.g. "Act as an expert").

OUTPUT FORMAT (Strictly Numbers Only):
Intent_Strength: [Score]
Clarity: [Score]
Specificity: [Score]
Context: [Score]
Constraints: [Score]
Complexity: [Score]
Role_Definition: [Score]"""


def extract_score(key: str, text: str) -> float:
    match = re.search(rf"{key}:\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else 0.0


def _llm_score_prompt(user_prompt: str) -> str:
    return _mistral_chat(
        system_message=SCORING_SYSTEM,
        user_message=f"INPUT PROMPT:\n{user_prompt}",
        temperature=0.1,
        max_tokens=256,
    )


def analyze_prompt_flow(user_prompt: str) -> dict[str, Any]:
    start = time.perf_counter()
    logger.info("Analyzing prompt: '%s'", truncate(user_prompt))

    check = is_valid_prompt(user_prompt)
    if not check["valid"]:
        logger.warning("Rejected (heuristic): %s", check["reason"])
        return {
            "bert_score": 0,
            "llm_score": 0,
            "final_score": 0,
            "status": "REJECTED",
            "msg": check["reason"],
        }

    bert_score = _bert_score_raw(user_prompt)

    try:
        content = _llm_score_prompt(user_prompt)

        intent_score = extract_score("Intent_Strength", content)
        role_score = extract_score("Role_Definition", content)

        quality_metrics = [
            "Clarity",
            "Specificity",
            "Context",
            "Constraints",
            "Complexity",
            "Role_Definition",
        ]
        total_quality = sum(extract_score(m, content) for m in quality_metrics)
        llm_quality_average = total_quality / len(quality_metrics)

        if intent_score < 20:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(
                "Rejected (no intent) | bert=%.1f llm=%.1f | %.0fms",
                bert_score,
                llm_quality_average,
                elapsed,
            )
            return {
                "bert_score": bert_score,
                "llm_score": llm_quality_average,
                "final_score": llm_quality_average,
                "status": "REJECTED",
                "msg": "Input appears to be content rather than an instruction.",
            }

        if role_score > 80:
            llm_quality_average = min(100, llm_quality_average * 1.1)

        final_score = (
            (bert_score * WEIGHT_BERT) + (llm_quality_average * WEIGHT_LLM)
        ) / TOTAL_WEIGHT

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "Accepted | bert=%.1f llm=%.1f final=%.2f | %.0fms",
            bert_score,
            llm_quality_average,
            final_score,
            elapsed,
        )

        return {
            "bert_score": bert_score,
            "llm_score": round(llm_quality_average, 1),
            "final_score": round(final_score, 2),
            "status": "ACCEPTED",
        }
    except Exception as exc:
        logger.error("Analysis error: %s", exc)
        return {"status": "ERROR", "msg": str(exc)}


def _clip_for_probe(text: str, limit: int = 400) -> str:
    """Keep raw user text intact; only clip length for probe payloads."""
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _is_meta_rewrite(text: str) -> bool:
    """Detect when the model echoed rewrite instructions instead of a real prompt."""
    lowered = text.lower()
    meta_markers = [
        "intent strength",
        "role definition",
        "score well on",
        "output only the rewritten",
        "rewritten prompt",
        "prompt engineering and task execution",
        "analyze the user's request, identify the core intent",
        "high-scoring structural patterns from bert",
        "prefer these high-scoring structures",
    ]
    hits = sum(1 for marker in meta_markers if marker in lowered)
    return hits >= 2


def _probe_bert_patterns(user_prompt: str) -> list[dict[str, Any]]:
    """Score structural shells wrapped around the *actual* user text — no topic guessing."""
    raw = _clip_for_probe(user_prompt)
    probes = [
        {
            "name": "role_task",
            "text": (
                "Act as an expert assistant. Complete the following user request "
                f'faithfully:\n"""{raw}"""\n'
                "Provide a clear, structured response."
            ),
        },
        {
            "name": "context_constraints",
            "text": (
                "Given the user's message below, complete the task with specific steps. "
                "Constraints: be concise, accurate, and actionable.\n"
                f'User message:\n"""{raw}"""'
            ),
        },
        {
            "name": "explicit_instruction",
            "text": (
                "Write a detailed response that fulfills this request. Include any "
                "needed background, requirements, and an explicit output format.\n"
                f'Request:\n"""{raw}"""'
            ),
        },
        {
            "name": "persona_depth",
            "text": (
                "You are a senior specialist. Analyze the request below, identify key "
                "constraints (or ask for missing ones), and deliver a thorough solution.\n"
                f'Request:\n"""{raw}"""'
            ),
        },
    ]

    results = []
    for probe in probes:
        score = _bert_score_raw(probe["text"])
        results.append({"name": probe["name"], "score": score, "text": probe["text"]})
        logger.info("Probe '%s' scored %.1f", probe["name"], score)

    results.sort(key=lambda item: item["score"], reverse=True)
    return results


def _build_probe_insights(probe_results: list[dict[str, Any]]) -> str:
    if not probe_results:
        return (
            "Structural preference: start with a role, state the task explicitly, "
            "add constraints, and specify the output format."
        )

    top = probe_results[:2]
    pattern_tips = {
        "role_task": "Lead with 'Act as...' then state the concrete task.",
        "context_constraints": "Add background context and explicit constraints.",
        "explicit_instruction": "Open with a strong verb (Write/Create/Analyze) and define deliverables.",
        "persona_depth": "Assign a senior specialist persona and ask for structured analysis.",
    }
    lines = [
        "Prefer these high-scoring structures (apply them to the user's actual content; "
        "do not copy example text):"
    ]
    for item in top:
        tip = pattern_tips.get(item["name"], "Use role + task + constraints.")
        lines.append(f"- {item['name']} (BERT {item['score']:.1f}): {tip}")
    return "\n".join(lines)


def _call_mistral_rewrite(
    user_prompt: str,
    probe_insights: str,
    *,
    previous_attempt: str | None = None,
    previous_score: float | None = None,
    iteration: int = 1,
    anti_meta: bool = False,
) -> str:
    feedback = ""
    if previous_attempt and previous_score is not None:
        feedback = (
            f"\nPrevious rewrite scored {previous_score:.1f} on our analyzer. "
            "Strengthen role, task clarity, constraints, and output format "
            "while keeping the user's meaning.\n"
            f"Previous attempt:\n{previous_attempt}\n"
        )

    anti_meta_block = ""
    if anti_meta:
        anti_meta_block = (
            "\nCRITICAL: Your previous output was invalid meta-instructions. "
            "Do NOT mention scoring metrics, prompt engineering rules, or "
            "'rewritten prompt'. Write a concrete task prompt grounded in the "
            "user's actual message.\n"
        )

    system_message = (
        "You rewrite any user message into a strong AI prompt.\n\n"
        "Hard rules:\n"
        "1. Ground the rewrite in the user's actual words/meaning — do not invent "
        "an unrelated topic.\n"
        "2. The output MUST be a usable prompt for an AI assistant, not instructions "
        "about how to rewrite prompts.\n"
        "3. NEVER mention Intent Strength, Clarity, Specificity, Context, "
        "Constraints, Complexity, Role Definition, BERT, scores, or 'rewritten prompt'.\n"
        "4. NEVER copy or paraphrase these system rules into the output.\n"
        "5. Start with an explicit role or imperative (Act as... / Write... / Analyze...).\n"
        "6. If intent is incomplete or ambiguous, keep the original meaning and add "
        "clarifying questions / missing-detail handling inside the rewritten prompt.\n"
        "7. Include constraints and an expected output format when useful.\n"
        "8. Output ONLY the final prompt text — no preface, no process commentary.\n\n"
        f"{probe_insights}\n"
        f"{feedback}"
        f"{anti_meta_block}"
    )

    content = _mistral_chat(
        system_message=system_message,
        user_message=(
            "Rewrite the following user message into a strong AI prompt. "
            "Preserve its meaning exactly; only improve structure and clarity.\n\n"
            f"USER MESSAGE:\n{user_prompt}"
        ),
        temperature=0.35 if anti_meta else 0.3,
        max_tokens=1024,
    )

    content = re.sub(r"^```(?:\w+)?\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    content = content.strip().strip('"').strip("'")

    content = re.sub(
        r"^(?:rewritten\s+prompt|improved\s+prompt|here(?:'s| is)\s+the\s+prompt)\s*[:\-]\s*",
        "",
        content,
        flags=re.IGNORECASE,
    ).strip()

    logger.info("Mistral rewrite iteration %d: '%s'", iteration, truncate(content))
    return content


def _deterministic_fallback_rewrite(user_prompt: str) -> str:
    """Structure-only fallback that embeds the raw user text — works for any input."""
    raw = user_prompt.strip()
    return (
        "Act as a professional assistant. Fulfill the user's request below. "
        "If anything required to proceed is missing or ambiguous, ask concise "
        "clarifying questions first; otherwise deliver a complete, actionable answer. "
        "Constraints: stay faithful to the user's intent; be clear and specific; "
        "state assumptions explicitly when needed. "
        "Output format: clarifying questions (only if needed), then the solution.\n\n"
        f'User request:\n"""{raw}"""'
    )


def _rewrite_with_validation(
    user_prompt: str,
    probe_insights: str,
    *,
    previous_attempt: str | None = None,
    previous_score: float | None = None,
    iteration: int = 1,
) -> str:
    rewritten = _call_mistral_rewrite(
        user_prompt,
        probe_insights,
        previous_attempt=previous_attempt,
        previous_score=previous_score,
        iteration=iteration,
    )
    if _is_meta_rewrite(rewritten):
        logger.warning("Meta rewrite detected on iteration %d — retrying", iteration)
        rewritten = _call_mistral_rewrite(
            user_prompt,
            probe_insights,
            previous_attempt=previous_attempt,
            previous_score=previous_score,
            iteration=iteration,
            anti_meta=True,
        )
        if _is_meta_rewrite(rewritten):
            rewritten = _deterministic_fallback_rewrite(user_prompt)
            logger.warning("Using structure-only fallback after meta leak")
    return rewritten


def _score_result(result: dict[str, Any]) -> float:
    return float(result.get("final_score", 0) or 0)


def improve_prompt_flow(
    user_prompt: str,
    *,
    known_score: float | None = None,
    known_status: str | None = None,
    known_bert: float | None = None,
    known_llm: float | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    logger.info("Improve pipeline started: '%s'", truncate(user_prompt))

    try:
        known_score = float(known_score) if known_score is not None else None
        known_bert = float(known_bert) if known_bert is not None else None
        known_llm = float(known_llm) if known_llm is not None else None
    except (TypeError, ValueError):
        known_score = known_bert = known_llm = None
        known_status = None

    # Prefer last UI analysis when available so a transient Mistral blip
    # does not wipe a good score (and so we can skip above-threshold prompts).
    if (
        known_status == "ACCEPTED"
        and known_score is not None
        and known_score >= TRASH_THRESHOLD
    ):
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "Skipped improve — known score %.2f already above threshold %.0f | %.0fms",
            known_score,
            TRASH_THRESHOLD,
            elapsed,
        )
        return {
            "status": "SKIPPED",
            "msg": f"Prompt already scores {known_score:.1f} (threshold: {TRASH_THRESHOLD:.0f}).",
            "original": user_prompt,
            "improved": user_prompt,
            "original_score": known_score,
            "final_score": known_score,
            "bert_score": known_bert or 0,
            "llm_score": known_llm or 0,
            "iterations": 0,
            "threshold": TRASH_THRESHOLD,
            "analysis_status": "ACCEPTED",
        }

    original_result = analyze_prompt_flow(user_prompt)

    if original_result.get("status") == "ERROR":
        # Fall back to known scores if re-score failed transiently
        if known_score is not None and known_status in ("ACCEPTED", "REJECTED"):
            logger.warning(
                "Re-score failed; using known score %.2f from prior analysis",
                known_score,
            )
            original_result = {
                "status": known_status,
                "final_score": known_score,
                "bert_score": known_bert or 0,
                "llm_score": known_llm or 0,
                "msg": "Used prior analysis after transient API error.",
            }
        else:
            return {
                "status": "ERROR",
                "msg": original_result.get("msg", "Failed to score prompt before improve."),
                "original": user_prompt,
                "improved": user_prompt,
                "original_score": 0,
                "final_score": 0,
                "bert_score": 0,
                "llm_score": 0,
                "iterations": 0,
                "threshold": TRASH_THRESHOLD,
            }

    original_score = _score_result(original_result)

    if original_result.get("status") == "ACCEPTED" and original_score >= TRASH_THRESHOLD:
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "Skipped improve — score %.2f already above threshold %.0f | %.0fms",
            original_score,
            TRASH_THRESHOLD,
            elapsed,
        )
        return {
            "status": "SKIPPED",
            "msg": f"Prompt already scores {original_score:.1f} (threshold: {TRASH_THRESHOLD:.0f}).",
            "original": user_prompt,
            "improved": user_prompt,
            "original_score": original_score,
            "final_score": original_score,
            "bert_score": original_result.get("bert_score", 0),
            "llm_score": original_result.get("llm_score", 0),
            "iterations": 0,
            "threshold": TRASH_THRESHOLD,
            "analysis_status": "ACCEPTED",
        }

    probe_results = _probe_bert_patterns(user_prompt)
    probe_insights = _build_probe_insights(probe_results)

    best_text = user_prompt
    best_result = original_result
    best_score = original_score
    previous_attempt: str | None = None
    previous_score: float | None = None
    iteration = 0

    for iteration in range(1, MAX_IMPROVE_ITERS + 1):
        try:
            rewritten = _rewrite_with_validation(
                user_prompt,
                probe_insights,
                previous_attempt=previous_attempt,
                previous_score=previous_score,
                iteration=iteration,
            )
        except Exception as exc:
            logger.error("Mistral rewrite failed on iteration %d: %s", iteration, exc)
            break

        rescored = analyze_prompt_flow(rewritten)
        if rescored.get("status") == "ERROR":
            logger.warning(
                "Rescore failed on iteration %d: %s",
                iteration,
                rescored.get("msg"),
            )
            previous_attempt = rewritten
            previous_score = previous_score if previous_score is not None else best_score
            continue

        new_score = _score_result(rescored)

        logger.info(
            "Improve iteration %d | score %.2f (best %.2f)",
            iteration,
            new_score,
            best_score,
        )

        if new_score > best_score:
            best_text = rewritten
            best_result = rescored
            best_score = new_score

        if new_score >= TARGET_SCORE:
            break

        previous_attempt = rewritten
        previous_score = new_score

    elapsed = (time.perf_counter() - start) * 1000
    improved_status = best_result.get("status", "ERROR")

    if best_score <= original_score:
        logger.warning(
            "Improve finished without gain | original=%.2f best=%.2f | %.0fms",
            original_score,
            best_score,
            elapsed,
        )
    else:
        logger.info(
            "Improve finished | original=%.2f -> %.2f | %d iterations | %.0fms",
            original_score,
            best_score,
            iteration,
            elapsed,
        )

    return {
        "status": "IMPROVED" if best_score > original_score else "UNCHANGED",
        "original": user_prompt,
        "improved": best_text,
        "original_score": original_score,
        "final_score": best_score,
        "bert_score": best_result.get("bert_score", 0),
        "llm_score": best_result.get("llm_score", 0),
        "analysis_status": improved_status,
        "msg": best_result.get("msg"),
        "iterations": iteration,
        "threshold": TRASH_THRESHOLD,
        "probe_top": probe_results[0]["name"] if probe_results else None,
    }
