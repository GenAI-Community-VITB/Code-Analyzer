"""Shared hybrid analysis (static + LLM + report) used by clone pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings, get_settings
from app.exceptions import EmptyRepositoryError
from app.models import AnalyzeResponse, FileLLMInsight
from app.services.llm_service import GeminiAnalyzer
from app.services.report_generator import (
    build_file_summary_entries,
    insights_from_llm_dict,
    safe_report_filename,
    write_report,
)
from app.services.scoring import WEIGHTS, compute_scores, flatten_issues
from app.services.static_analyzer import aggregate_repo, analyze_file, detect_language

logger = logging.getLogger(__name__)


async def run_hybrid_analysis_pipeline(
    owner: str,
    repo_name: str,
    raw_contents: dict[str, str],
    size_map: dict[str, int],
    settings: Settings | None = None,
    *,
    default_branch: str | None = None,
    analysis_mode: str = "git_clone",
) -> AnalyzeResponse:
    """
    Run static analysis (parallel), batched LLM, scoring, and write report.

    `raw_contents` keys are repository-relative paths.
    """
    s = settings or get_settings()

    paths_order = list(raw_contents.keys())

    async def run_one(path: str):
        return await asyncio.to_thread(
            analyze_file,
            path,
            raw_contents[path],
            size_map.get(path, 0),
        )

    static_results = await asyncio.gather(*[run_one(p) for p in paths_order])

    file_results = []
    paths_ok: list[str] = []
    loc_map: dict[str, int] = {}

    for path, result in zip(paths_order, static_results):
        if result.lines_of_code == 0:
            continue
        file_results.append(result)
        paths_ok.append(path)
        loc_map[path] = result.lines_of_code

    if not file_results:
        raise EmptyRepositoryError("All candidate files were empty or unreadable")

    agg = aggregate_repo(paths_ok, file_results)
    score = compute_scores(agg)
    issues = flatten_issues(agg)

    gemini = GeminiAnalyzer(s)
    llm_targets = paths_ok[: s.max_files_for_llm]

    batch_size = max(1, min(s.llm_batch_size, 12))
    chunks = [llm_targets[i : i + batch_size] for i in range(0, len(llm_targets), batch_size)]

    batch_sem = asyncio.Semaphore(max(1, s.llm_concurrent_batches))

    async def process_llm_chunk(chunk: list[str]) -> list[FileLLMInsight]:
        out: list[FileLLMInsight] = []
        if not chunk:
            return out
        if not gemini.enabled:
            for path in chunk:
                out.append(
                    FileLLMInsight(
                        path=path,
                        summary="LLM analysis skipped (GEMINI_API_KEY not configured).",
                        issues=[],
                        suggestions=[],
                    ),
                )
            return out

        async with batch_sem:
            batch_inputs: list[tuple[str, str, str]] = []
            for path in chunk:
                content = raw_contents.get(path, "")[: s.max_chars_per_file_llm]
                batch_inputs.append((path, detect_language(path), content))
            try:
                batch_out = await gemini.analyze_files_batch(batch_inputs)
            except Exception as e:
                logger.warning("Batch LLM failed (%s); falling back per file: %s", chunk[0], e)
                batch_out = []

            by_path = {str(d.get("path")): d for d in batch_out if isinstance(d, dict)}

            for idx, (path, lang, _content) in enumerate(batch_inputs):
                data = by_path.get(path)
                if not data and idx < len(batch_out) and isinstance(batch_out[idx], dict):
                    data = batch_out[idx]
                if not data:
                    try:
                        data = await gemini.analyze_file(
                            path,
                            lang,
                            raw_contents.get(path, "")[: s.max_chars_per_file_llm],
                        )
                    except Exception as e2:
                        logger.error("LLM file analysis failed for %s: %s", path, e2)
                        out.append(
                            FileLLMInsight(
                                path=path,
                                summary="LLM analysis failed for this file.",
                                issues=[str(e2)],
                                suggestions=[],
                            ),
                        )
                        continue
                out.append(insights_from_llm_dict(path, data))
        return out

    chunk_results = await asyncio.gather(*[process_llm_chunk(c) for c in chunks])
    file_insights: list[FileLLMInsight] = []
    for part in chunk_results:
        file_insights.extend(part)

    llm_summaries: list[dict[str, Any]] = [
        {
            "path": fi.path,
            "summary": fi.summary,
            "issues": fi.issues[:5],
            "suggestions": fi.suggestions[:5],
        }
        for fi in file_insights
    ]

    static_sample = issues[:40]
    design_summary = ""
    repo_level = ""

    if gemini.enabled:
        try:
            repo_json = await gemini.analyze_repository_design(owner, repo_name, llm_summaries, static_sample)
            design_summary = str(repo_json.get("design_summary") or "").strip()
            repo_level = design_summary
        except Exception as e:
            logger.error("LLM design summary failed: %s", e)
            design_summary = f"Design assessment unavailable: {e}"
            repo_level = design_summary
    else:
        design_summary = (
            "Set GEMINI_API_KEY for a short automated design description; scores and findings use static analysis."
        )
        repo_level = design_summary

    report_name = safe_report_filename(repo_name)
    report_path = s.reports_dir / report_name

    meta: dict[str, Any] = {
        "files_analyzed": len(paths_ok),
        "files_considered": len(raw_contents),
        "llm_enabled": gemini.enabled,
        "analysis_mode": analysis_mode,
    }
    if default_branch is not None:
        meta["default_branch"] = default_branch

    response = AnalyzeResponse(
        repository=f"{owner}/{repo_name}",
        owner=owner,
        repo_name=repo_name,
        report_file=str(report_path.resolve()),
        overall_score=score.overall,
        scores=score.dimensions,
        weights_applied=WEIGHTS.copy(),
        file_summary=build_file_summary_entries(paths_ok, loc_map, size_map),
        issues_found=issues,
        suggestions=[],
        architecture_feedback="",
        documentation_review="",
        design_summary=design_summary,
        file_insights=file_insights,
        repo_level_insights=repo_level,
        metadata=meta,
    )

    write_report(report_path, response)
    return response
