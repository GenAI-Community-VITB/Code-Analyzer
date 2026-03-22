"""Walk a cloned repository and collect analyzable file paths."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import Settings
from app.services.file_filter import (
    clone_priority_sort_key,
    path_has_excluded_segment,
    should_analyze_path,
)

logger = logging.getLogger(__name__)


def discover_analyzable_files(repo_root: Path, settings: Settings) -> list[tuple[str, int]]:
    """
    Return sorted list of (repo-relative posix path, size_bytes) within caps.
    """
    repo_root = repo_root.resolve()
    out: list[tuple[str, int]] = []

    for dirpath, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _prune_dir(d)]

        for name in filenames:
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(repo_root)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            if path_has_excluded_segment(rel_posix):
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            size = int(st.st_size)
            if not should_analyze_path(rel_posix, size, settings.max_file_bytes):
                continue
            out.append((rel_posix, size))

    out.sort(key=lambda x: clone_priority_sort_key(x[0]))
    limit = settings.max_files_clone_analysis
    if len(out) > limit:
        logger.warning(
            "Discovered %s analyzable files; capping to %s (clone pipeline limit).",
            len(out),
            limit,
        )
        out = out[:limit]
    return out


def _prune_dir(name: str) -> bool:
    """Return True if this directory name should not be entered."""
    ln = name.lower()
    if ln == ".git":
        return True
    if ln == "node_modules":
        return True
    if ln in {
        "dist",
        "build",
        "target",
        ".next",
        "out",
        "coverage",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".pytest_cache",
        "bower_components",
        "vendor",
    }:
        return True
    if ln.startswith(".") and ln not in {".github"}:
        return True
    return False
