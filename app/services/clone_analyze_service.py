"""Clone-based analysis: shallow git clone, local scan, hybrid analysis, guaranteed cleanup."""

from __future__ import annotations

import asyncio
import gc
import logging
import uuid
from pathlib import Path

from app.config import Settings, get_settings
from app.exceptions import EmptyRepositoryError
from app.models import AnalyzeResponse
from app.services.git_clone import remove_clone, shallow_clone
from app.services.local_discovery import discover_analyzable_files
from app.services.pipeline_core import run_hybrid_analysis_pipeline
from app.services.url_parser import parse_github_repo_url

logger = logging.getLogger(__name__)


def _clone_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


async def analyze_repository_via_clone(repo_url: str, settings: Settings | None = None, depth: str = "full") -> AnalyzeResponse:
    """
    Shallow clone, analyze up to max_files_clone_analysis files, delete clone in all cases.
    """
    s = settings or get_settings()
    owner, repo_name = parse_github_repo_url(repo_url)
    clone_dest = (s.clone_work_dir / f"{owner}_{repo_name}_{uuid.uuid4().hex[:10]}").resolve()

    logger.info("Clone pipeline starting for %s/%s -> %s", owner, repo_name, clone_dest)

    try:
        await asyncio.to_thread(
            shallow_clone,
            _clone_url(owner, repo_name),
            clone_dest,
            s.git_clone_timeout_seconds,
        )

        discovered = await asyncio.to_thread(discover_analyzable_files, clone_dest, s)
        if not discovered:
            raise EmptyRepositoryError("No files matched analysis filters")

        raw_contents: dict[str, str] = {}
        size_map: dict[str, int] = {}
        read_sem = asyncio.Semaphore(64)

        async def read_one(rel: str, sz: int) -> None:
            async with read_sem:
                fp = clone_dest / Path(rel)
                try:
                    text = await asyncio.to_thread(
                        fp.read_text,
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError as e:
                    logger.warning("Unreadable file skipped: %s (%s)", rel, e)
                    return
            if not text.strip():
                return
            raw_contents[rel] = text
            size_map[rel] = sz

        await asyncio.gather(*(read_one(rel, sz) for rel, sz in discovered))

        if not raw_contents:
            raise EmptyRepositoryError("All candidate files were empty or unreadable")

        logger.info("Loaded %s files from clone; running hybrid analysis", len(raw_contents))
        return await run_hybrid_analysis_pipeline(
            owner,
            repo_name,
            raw_contents,
            size_map,
            s,
            default_branch=None,
            analysis_mode="git_clone",
            depth=depth,
        )
    finally:
        gc.collect()
        await asyncio.to_thread(remove_clone, clone_dest)
