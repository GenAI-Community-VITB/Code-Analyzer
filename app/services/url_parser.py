"""Parse and validate GitHub repository URLs."""

import re
from urllib.parse import urlparse

from app.exceptions import InvalidRepositoryURLError

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_PATH_PATTERN = re.compile(
    r"^/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)(?:/|$)",
    re.IGNORECASE,
)


def parse_github_repo_url(url: str) -> tuple[str, str]:
    """
    Extract (owner, repo) from a GitHub HTTPS or SSH URL.

    Raises InvalidRepositoryURLError on invalid input.
    """
    if not url or not isinstance(url, str):
        raise InvalidRepositoryURLError("Repository URL is required")

    raw = url.strip()
    if not raw:
        raise InvalidRepositoryURLError("Repository URL is empty")

    # git@github.com:owner/repo.git
    if raw.startswith("git@"):
        match = re.match(r"git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?$", raw, re.I)
        if not match:
            raise InvalidRepositoryURLError("Invalid GitHub SSH URL format")
        owner = match.group("owner")
        repo = _strip_git_suffix(match.group("repo"))
        return owner, repo

    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise InvalidRepositoryURLError("URL must use http or https")

    host = (parsed.hostname or "").lower()
    if host not in _GITHUB_HOSTS:
        raise InvalidRepositoryURLError("Only github.com repository URLs are supported")

    path_match = _PATH_PATTERN.match(parsed.path or "")
    if not path_match:
        raise InvalidRepositoryURLError(
            "URL must look like https://github.com/owner/repository",
        )

    owner = path_match.group("owner")
    repo = _strip_git_suffix(path_match.group("repo"))
    if not owner or not repo:
        raise InvalidRepositoryURLError("Could not parse owner or repository name")

    return owner, repo


def _strip_git_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".git") else name
