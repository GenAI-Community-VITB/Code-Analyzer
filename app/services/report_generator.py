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
    """Formatter to strictly match user-requested sections."""
    lines: list[str] = []
    
    lines.append("1. Repository Overview")
    lines.append("=" * 22)
    lines.append(f"Repository: {p.repository}")
    lines.append(f"Overall score: {p.overall_score:.1f} / 100")
    if p.metadata:
        fa = p.metadata.get("files_analyzed")
        if fa is not None:
            lines.append(f"Files evaluated: {fa}")
    lines.append("")

    lines.append("2. File Summary (file -> LOC)")
    lines.append("=" * 29)
    if not p.file_summary:
        lines.append("  (none)")
    else:
        for f in p.file_summary:
            lines.append(f"  {f.path} -> {f.lines_of_code} LOC")
    lines.append("")

    lines.append("3. Scores Breakdown")
    lines.append("=" * 19)
    s: DimensionScores = p.scores
    w = p.weights_applied
    lines.append(f"  Security ({w.get('security', 0):.0%}): {s.security:.1f}")
    lines.append(f"  Code Quality ({w.get('code_quality', 0):.0%}): {s.code_quality:.1f}")
    lines.append(f"  Design ({w.get('design', 0):.0%}): {s.design:.1f}")
    lines.append(f"  Structure ({w.get('structure', 0):.0%}): {s.structure:.1f}")
    lines.append(f"  Naming ({w.get('naming', 0):.0%}): {s.naming:.1f}")
    lines.append(f"  Documentation ({w.get('documentation', 0):.0%}): {s.documentation:.1f}")
    lines.append(f"  Testing ({w.get('testing', 0):.0%}): {s.testing:.1f}")
    lines.append(f"  Performance ({w.get('performance', 0):.0%}): {s.performance:.1f}")
    lines.append("")
    
    if p.metadata and p.metadata.get("analysis_depth") == "basic":
        return "\n".join(lines).rstrip() + "\n"

    lines.append("4. Key Issues")
    lines.append("=" * 13)
    if not p.issues_found:
        lines.append("  (none flagged)")
    else:
        for issue in p.issues_found[:150]:
            lines.append(f"  • {issue}")
        
    for file_ins in p.file_insights:
        if file_ins.issues:
            for issue in file_ins.issues:
                lines.append(f"  • [{file_ins.path}] {issue}")
    lines.append("")

    lines.append("5. Suggestions")
    lines.append("=" * 14)
    if not p.suggestions:
        lines.append("  (none at repo level)")
    else:
        for sug in p.suggestions:
            lines.append(f"  • {sug}")
            
    for file_ins in p.file_insights:
        if file_ins.suggestions:
            for sug in file_ins.suggestions:
                lines.append(f"  • [{file_ins.path}] {sug}")
    lines.append("")

    lines.append("6. Architecture Analysis")
    lines.append("=" * 24)
    ds = (p.design_summary or "").strip()
    if not ds:
        ds = (p.repo_level_insights or "").strip()
    if ds:
        for ln in ds.splitlines():
            lines.append(f"  {ln}")
    else:
        lines.append("  (not available)")
    lines.append("")

    lines.append("7. Documentation Review")
    lines.append("=" * 23)
    if p.documentation_review:
        lines.append(f"  {p.documentation_review}")
    else:
        lines.append("  (handled primarily within section 3 and 4)")
    lines.append("")

    lines.append("8. Token Usage & Cost")
    lines.append("=" * 21)
    lines.append(f"  Total Input Tokens: {p.total_input_tokens}")
    lines.append(f"  Total Output Tokens: {p.total_output_tokens}")
    lines.append(f"  Estimated Cost: ${p.estimated_cost_usd:.5f}")
    lines.append("")

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
