"""Compute weighted 0–100 scores from static aggregate and file results."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import DimensionScores
from app.services.static_analyzer import RepositoryStaticAggregate

WEIGHTS: dict[str, float] = {
    "security": 0.30,
    "code_quality": 0.20,
    "design": 0.15,
    "structure": 0.10,
    "naming": 0.10,
    "documentation": 0.05,
    "testing": 0.05,
    "performance": 0.05,
}


@dataclass
class ScoreComputation:
    dimensions: DimensionScores
    overall: float


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, round(score, 2)))


def compute_scores(agg: RepositoryStaticAggregate) -> ScoreComputation:
    files = agg.file_results
    n_files = max(1, len(files))

    sec_issues = sum(len(fr.security_flags) for fr in files)
    security = 100.0 - min(80.0, sec_issues * 12.0)

    cq_issues = sum(len(fr.quality_flags) for fr in files)
    code_quality = 100.0 - min(75.0, cq_issues * 6.0)

    des_issues = sum(len(fr.design_flags) for fr in files)
    design = 100.0 - min(70.0, des_issues * 8.0)

    # Structure: manifests + layered folders + low duplication
    structure_score = 40.0
    if agg.has_requirements or agg.has_package_json or agg.has_go_mod or agg.has_pom:
        structure_score += 20.0
    if agg.structure_paths:
        structure_score += min(25.0, len(set(agg.structure_paths)) * 3.0)
    if agg.duplicate_pairs:
        structure_score -= min(30.0, len(agg.duplicate_pairs) * 4.0)
    structure = _clamp(structure_score)

    name_issues = sum(len(fr.naming_flags) for fr in files)
    naming = 100.0 - min(70.0, name_issues * 5.0)

    doc_issues = sum(len(fr.doc_flags) for fr in files)
    doc_bonus = 15.0 if agg.has_readme else 0.0
    documentation = _clamp(85.0 - doc_issues * 5.0 + doc_bonus - (0 if agg.has_readme else 15.0))

    test_count = len(agg.test_file_paths)
    testing = _clamp(40.0 + min(55.0, test_count * 12.0))

    perf_issues = sum(len(fr.perf_flags) for fr in files)
    performance = 100.0 - min(60.0, perf_issues * 10.0)

    dimensions = DimensionScores(
        security=_clamp(security),
        code_quality=_clamp(code_quality),
        design=_clamp(design),
        structure=_clamp(structure),
        naming=_clamp(naming),
        documentation=_clamp(documentation),
        testing=_clamp(testing),
        performance=_clamp(performance),
    )

    overall = sum(
        getattr(dimensions, k) * w for k, w in WEIGHTS.items()
    )
    overall = _clamp(overall)

    return ScoreComputation(dimensions=dimensions, overall=overall)


def flatten_issues(agg: RepositoryStaticAggregate) -> list[str]:
    issues: list[str] = []
    for fr in agg.file_results:
        for flag in fr.security_flags:
            issues.append(f"[Security] {fr.path}: {flag}")
        for flag in fr.quality_flags:
            issues.append(f"[Quality] {fr.path}: {flag}")
        for flag in fr.design_flags:
            issues.append(f"[Design] {fr.path}: {flag}")
        for flag in fr.naming_flags:
            issues.append(f"[Naming] {fr.path}: {flag}")
        for flag in fr.doc_flags:
            issues.append(f"[Documentation] {fr.path}: {flag}")
        for flag in fr.perf_flags:
            issues.append(f"[Performance] {fr.path}: {flag}")
    for a, b, jac in agg.duplicate_pairs[:15]:
        issues.append(f"[Duplication] Similar content between '{a}' and '{b}' (Jaccard ~{jac})")
    if not agg.has_readme:
        issues.append("[Documentation] No README.md found at repository root")
    if not (agg.has_requirements or agg.has_package_json or agg.has_go_mod or agg.has_pom):
        issues.append("[Dependencies] No requirements.txt, package.json, go.mod, or pom.xml detected")
    if not agg.test_file_paths:
        issues.append("[Testing] No obvious test files or test directories detected")
    return issues[:200]
