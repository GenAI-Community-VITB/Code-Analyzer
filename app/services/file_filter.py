"""Filter repository tree entries to backend/core analyzable files."""

from pathlib import PurePosixPath

# Extensions to include (lowercase, with dot)
_INCLUDED_EXTENSIONS = frozenset({
    ".py",
    ".js",
    ".ts",
    ".java",
    ".go",
    ".cpp",
    ".c",
    ".rs",
    ".yml",
    ".yaml",
    ".md",
})

_EXCLUDED_NAME_PARTS = (
    ".min.",
    ".bundle.",
    ".chunk.",
)

_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
})

_FRONTEND_HEAVY = frozenset({
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".jsx", ".tsx",
})


def should_analyze_path(path: str, size_bytes: int | None, max_bytes: int) -> bool:
    """
    Return True if the path should be analyzed per project rules.

    size_bytes: from GitHub tree blob when available; None if unknown.
    """
    if not path or path.strip() == "":
        return False

    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    lower = normalized.lower()
    suffix = PurePosixPath(normalized).suffix.lower()

    if size_bytes is not None and size_bytes > max_bytes:
        return False

    if suffix in _FRONTEND_HEAVY:
        return False
    if suffix in _IMAGE_EXTENSIONS:
        return False

    for part in _EXCLUDED_NAME_PARTS:
        if part in name.lower():
            return False

    # Dockerfile (any common casing)
    if name.lower() == "dockerfile" or name.lower().startswith("dockerfile."):
        return True

    if suffix in _INCLUDED_EXTENSIONS:
        return True

    return False


def priority_sort_key(path: str) -> tuple[int, str]:
    """Prefer code over docs; shorter paths first for stability."""
    p = path.lower()
    if p.endswith(".md"):
        return (2, path)
    return (1, path)


_EXCLUDED_PATH_SEGMENTS = frozenset({
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    "out",
    "coverage",
    "bower_components",
    "vendor",
})


def path_has_excluded_segment(relative_path: str) -> bool:
    """Defensive check when a path still references excluded folders."""
    parts = relative_path.replace("\\", "/").lower().split("/")
    return any(p in _EXCLUDED_PATH_SEGMENTS for p in parts)


def clone_priority_sort_key(path: str) -> tuple[int, int, str]:
    """
    Prefer core backend paths (src/, app/, backend/, services/), then other code, then markdown.
    """
    p = path.replace("\\", "/").lower()
    is_md = p.endswith(".md")
    doc_rank = 2 if is_md else 0
    core_markers = ("/src/", "/app/", "/backend/", "/services/")
    in_core = any(m in p for m in core_markers) or p.startswith(
        ("src/", "app/", "backend/", "services/"),
    )
    core_rank = 0 if in_core else 1
    return (doc_rank, core_rank, path)
