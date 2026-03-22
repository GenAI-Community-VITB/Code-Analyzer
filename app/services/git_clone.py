"""Shallow git clone helpers with clear error mapping."""

from __future__ import annotations

import gc
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from app.exceptions import CloneFailedError, RepositoryInaccessibleError

logger = logging.getLogger(__name__)


def ensure_git_available() -> None:
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise CloneFailedError(
            "Git executable not found or not working. Install Git and ensure it is on PATH.",
        ) from e


def shallow_clone(repo_url: str, dest: Path, timeout_seconds: float) -> None:
    """
    git clone --depth 1 --single-branch <url> <dest>

    Raises RepositoryInaccessibleError for missing/private/auth issues.
    Raises CloneFailedError for other failures.
    """
    ensure_git_available()
    dest = dest.resolve()
    if dest.exists():
        remove_clone(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", "1", "--single-branch", repo_url, str(dest)]
    logger.info("Starting shallow clone: %s -> %s", repo_url, dest)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CloneFailedError(f"Git clone timed out after {timeout_seconds:.0f}s") from e

    if proc.returncode == 0:
        logger.info("Clone completed: %s", dest)
        return

    err = f"{proc.stderr or ''}\n{proc.stdout or ''}".lower()
    full = (proc.stderr or "") + (proc.stdout or "")

    if "could not find remote" in err or "repository not found" in err or "does not exist" in err:
        raise RepositoryInaccessibleError("Repository is private or inaccessible")
    if "authentication failed" in err or "could not read from remote" in err or "access denied" in err:
        raise RepositoryInaccessibleError("Repository is private or inaccessible")
    if "permission denied" in err and "publickey" in err:
        raise RepositoryInaccessibleError("Repository is private or inaccessible")

    raise CloneFailedError(f"Git clone failed (exit {proc.returncode}): {full.strip()[:800]}")


def remove_clone(path: Path) -> None:
    """
    Delete a clone directory. Retries chmod on Windows, then cmd rmdir fallback.
    """
    p = path.resolve()
    if not p.exists():
        return

    gc.collect()
    time.sleep(0.05)

    def _chmod_retry(func: object, fpath: str, _exc: object) -> None:
        try:
            os.chmod(fpath, stat.S_IWRITE)
            if callable(func):
                func(fpath)
        except Exception:
            pass

    for attempt in range(4):
        try:
            shutil.rmtree(p, onerror=_chmod_retry)
            if not p.exists():
                logger.info("Removed clone directory: %s", p)
                return
        except OSError as e:
            logger.warning("rmtree attempt %s failed for %s: %s", attempt + 1, p, e)
        time.sleep(0.15 * (attempt + 1))
        gc.collect()

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["cmd", "/c", "rmdir", "/s", "/q", str(p)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Windows rmdir fallback: %s", e)

    if p.exists():
        logger.error("Could not remove clone directory (close editors/AV scanning this path): %s", p)
    else:
        logger.info("Removed clone directory: %s", p)
