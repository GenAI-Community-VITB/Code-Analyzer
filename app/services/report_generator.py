"""Write evaluation-focused reports (scores, findings, short design note)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.models import AnalyzeResponse, DimensionScores, FileLOCEntry, FileLLMInsight

logger = logging.getLogger(__name__)


def safe_report_filename(repo_name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in repo_name.strip())
    return f"{cleaned}_report.txt"


def write_report(path: Path, payload: AnalyzeResponse) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_report_text(payload)
    path.write_text(text, encoding="utf-8")
    logger.info("Report written to %s", path)


def build_report_text(p: AnalyzeResponse) -> str:
    """Scores, findings, and a brief design description only."""
    lines: list[str] = []
    lines.append(f"Repository: {p.repository}")
    lines.append(f"Overall score: {p.overall_score:.1f} / 100")
    if p.metadata:
        fa = p.metadata.get("files_analyzed")
        if fa is not None:
            lines.append(f"Files evaluated: {fa}")
    lines.append("")

    lines.append("Scores")
    s: DimensionScores = p.scores
    w = p.weights_applied
    lines.append(f"  Security ({w.get('security', 0):.0%}): {s.security:.1f}")
    lines.append(f"  Code quality ({w.get('code_quality', 0):.0%}): {s.code_quality:.1f}")
    lines.append(f"  Design ({w.get('design', 0):.0%}): {s.design:.1f}")
    lines.append(f"  Structure ({w.get('structure', 0):.0%}): {s.structure:.1f}")
    lines.append(f"  Naming ({w.get('naming', 0):.0%}): {s.naming:.1f}")
    lines.append(f"  Documentation ({w.get('documentation', 0):.0%}): {s.documentation:.1f}")
    lines.append(f"  Testing ({w.get('testing', 0):.0%}): {s.testing:.1f}")
    lines.append(f"  Performance ({w.get('performance', 0):.0%}): {s.performance:.1f}")
    lines.append("")

    lines.append("Findings")
    if not p.issues_found:
        lines.append("  (none flagged)")
    else:
        for issue in p.issues_found[:150]:
            lines.append(f"  • {issue}")
    lines.append("")

    lines.append("Design")
    ds = (p.design_summary or "").strip()
    if not ds:
        ds = (p.repo_level_insights or "").strip()
    if ds:
        for ln in ds.splitlines():
            lines.append(f"  {ln}")
    else:
        lines.append("  (not available)")

    return "\n".join(lines).rstrip() + "\n"


def build_file_summary_entries(
    paths: list[str],
    loc_map: dict[str, int],
    size_map: dict[str, int],
) -> list[FileLOCEntry]:
    out: list[FileLOCEntry] = []
    for path in sorted(paths):
        out.append(
            FileLOCEntry(
                path=path,
                lines_of_code=loc_map.get(path, 0),
                size_bytes=size_map.get(path, 0),
            ),
        )
    return out


def insights_from_llm_dict(path: str, data: dict) -> FileLLMInsight:
    issues = data.get("issues") or []
    suggestions = data.get("suggestions") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    if not isinstance(suggestions, list):
        suggestions = [str(suggestions)]
    return FileLLMInsight(
        path=path,
        summary=str(data.get("summary") or "")[:4000],
        issues=[str(x) for x in issues][:25],
        suggestions=[str(x) for x in suggestions][:25],
    )
