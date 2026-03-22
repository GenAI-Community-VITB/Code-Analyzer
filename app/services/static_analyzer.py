"""Multi-dimensional static analysis heuristics (no LLM)."""

from __future__ import annotations

import ast
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

# --- Security patterns ---
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]"),
    re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"(?i)aws_secret_access_key"),
    re.compile(r"(?i)ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)github_pat_[a-zA-Z0-9_]+"),
]

_UNSAFE_CALLS = [
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"(?i)\bos\.system\s*\("),
    re.compile(r"(?i)\bsubprocess\.\w+\([^)]*shell\s*=\s*True"),
    re.compile(r"(?i)\bpickle\.loads\s*\("),
    re.compile(r"(?i)\byaml\.load\s*\((?!.*Loader\s*=\s*yaml\.SafeLoader)"),
]

_INJECTION_HINTS = [
    re.compile(r'(?i)(execute|cursor\.execute)\s*\(\s*["\'][^"\']*%[^"\']*["\']'),
    re.compile(r"(?i)SELECT\s+.*\+\s*(\w+|['\"])"),
    re.compile(r"(?i)INSERT\s+INTO\s+.*\+\s*"),
]

_INSECURE_CONFIG = [
    re.compile(r"(?i)DEBUG\s*=\s*True"),
    re.compile(r"(?i)verify\s*=\s*False"),
    re.compile(r"(?i)ssl[_-]?verify\s*=\s*False"),
]

# Magic numbers: standalone digits in code (exclude common 0,1,2)
_MAGIC_NUMBER = re.compile(r"(?<![\w.])([3-9]\d{2,}|\d{4,})(?![\w.])")

# Naming
_SHORT_NAME = re.compile(r"^[a-z]$|^[a-z][0-9]$")


@dataclass
class FileStaticResult:
    path: str
    language: str
    lines_of_code: int
    size_bytes: int
    security_flags: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    naming_flags: list[str] = field(default_factory=list)
    design_flags: list[str] = field(default_factory=list)
    structure_hints: list[str] = field(default_factory=list)
    doc_flags: list[str] = field(default_factory=list)
    dep_flags: list[str] = field(default_factory=list)
    test_flags: list[str] = field(default_factory=list)
    perf_flags: list[str] = field(default_factory=list)
    normalized_lines: frozenset[str] = field(default_factory=frozenset)


@dataclass
class RepositoryStaticAggregate:
    file_results: list[FileStaticResult]
    duplicate_pairs: list[tuple[str, str, float]]
    has_requirements: bool
    has_package_json: bool
    has_go_mod: bool
    has_pom: bool
    has_readme: bool
    readme_paths: list[str]
    env_var_mentions: int
    hardcoded_url_or_host: int
    test_file_paths: list[str]
    structure_paths: list[str]


def detect_language(path: str) -> str:
    lower = path.lower()
    if lower.endswith("dockerfile") or "dockerfile" in PurePosixPath(lower).name.lower():
        return "docker"
    ext = PurePosixPath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
        ".go": "go",
        ".cpp": "cpp",
        ".c": "c",
        ".rs": "rust",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".md": "markdown",
    }.get(ext, "unknown")


def analyze_file(path: str, content: str, size_bytes: int) -> FileStaticResult:
    if not content.strip():
        return FileStaticResult(
            path=path,
            language=detect_language(path),
            lines_of_code=0,
            size_bytes=size_bytes,
            normalized_lines=frozenset(),
        )

    lines = content.splitlines()
    loc = len([ln for ln in lines if ln.strip()])
    lang = detect_language(path)
    norm_lines = _normalized_lines(lines)

    result = FileStaticResult(
        path=path,
        language=lang,
        lines_of_code=loc,
        size_bytes=size_bytes,
        normalized_lines=frozenset(norm_lines),
    )

    if lang == "markdown":
        _analyze_markdown(path, content, result)
        return result

    _scan_security(content, result)
    _scan_quality(content, lang, result)
    _scan_naming(content, lang, result)
    _scan_design(content, lang, result)
    _scan_docs(content, lang, result)
    _scan_deps_config(path, content, lang, result)
    _scan_tests(path, content, lang, result)
    _scan_performance(content, lang, result)

    if lang == "python":
        _analyze_python_ast(content, result)

    return result


def aggregate_repo(
    paths: list[str],
    file_results: list[FileStaticResult],
) -> RepositoryStaticAggregate:
    """Cross-file duplication and manifest detection."""
    has_req = any(
        p.lower().endswith("requirements.txt") or "requirements-" in p.lower()
        for p in paths
    )
    has_pkg = any(PurePosixPath(p).name.lower() == "package.json" for p in paths)
    has_go = any(PurePosixPath(p).name.lower() == "go.mod" for p in paths)
    has_pom = any(PurePosixPath(p).name.lower() == "pom.xml" for p in paths)
    readme_paths = [p for p in paths if PurePosixPath(p).name.upper() == "README.MD"]

    env_hits = sum(
        1 for fr in file_results
        if any("environment variables" in f for f in fr.dep_flags)
    )
    hardcoded = sum(
        1 for fr in file_results
        if any("hardcoded host" in f for f in fr.dep_flags)
    )

    test_paths = [
        p for p in paths
        if re.search(r"(/tests?/|/test/|__tests__|_test\.py|\.test\.|\.spec\.)", p, re.I)
        or PurePosixPath(p).name.startswith("test_")
        or PurePosixPath(p).name.endswith("_test.py")
    ]

    norm = [p.replace("\\", "/") for p in paths]
    structure_paths = [
        p for p in norm
        if re.search(
            r"(?:^|/)(routes?|controllers?|services?|models?|handlers?|api)(?:/|$)",
            p,
            re.I,
        )
    ]

    dup_pairs = _find_duplicates(file_results)

    return RepositoryStaticAggregate(
        file_results=file_results,
        duplicate_pairs=dup_pairs,
        has_requirements=has_req,
        has_package_json=has_pkg,
        has_go_mod=has_go,
        has_pom=has_pom,
        has_readme=len(readme_paths) > 0,
        readme_paths=readme_paths,
        env_var_mentions=env_hits,
        hardcoded_url_or_host=hardcoded,
        test_file_paths=test_paths,
        structure_paths=structure_paths,
    )


def _normalized_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return out


def _find_duplicates(file_results: list[FileStaticResult]) -> list[tuple[str, str, float]]:
    """Jaccard similarity on normalized lines between file pairs (same language only)."""
    by_lang: dict[str, list[FileStaticResult]] = defaultdict(list)
    for fr in file_results:
        if fr.lines_of_code < 15:
            continue
        if fr.language in ("markdown", "yaml", "unknown"):
            continue
        by_lang[fr.language].append(fr)

    pairs: list[tuple[str, str, float]] = []
    for lang, items in by_lang.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if not a.normalized_lines or not b.normalized_lines:
                    continue
                inter = len(a.normalized_lines & b.normalized_lines)
                union = len(a.normalized_lines | b.normalized_lines)
                if union == 0:
                    continue
                jaccard = inter / union
                if jaccard >= 0.22 and inter >= 8:
                    pairs.append((a.path, b.path, round(jaccard, 3)))
    pairs.sort(key=lambda x: -x[2])
    return pairs[:25]


def _scan_security(content: str, result: FileStaticResult) -> None:
    for pat in _SECRET_PATTERNS:
        if pat.search(content):
            result.security_flags.append("Possible hardcoded secret or credential pattern")
            break
    for pat in _UNSAFE_CALLS:
        if pat.search(content):
            result.security_flags.append(f"Potentially unsafe call: {pat.pattern[:50]}")
    for pat in _INJECTION_HINTS:
        if pat.search(content):
            result.security_flags.append("Possible injection risk (string-built query or command)")
    for pat in _INSECURE_CONFIG:
        if pat.search(content):
            result.security_flags.append("Insecure configuration pattern (debug/ssl)")


def _scan_quality(content: str, lang: str, result: FileStaticResult) -> None:
    lines = content.splitlines()
    max_indent = 0
    for ln in lines:
        if not ln.strip():
            continue
        stripped = ln.lstrip()
        indent = len(ln) - len(stripped)
        if stripped.startswith(("def ", "function ", "func ", "public ", "private ")):
            pass
        max_indent = max(max_indent, indent // 4)

    if max_indent >= 5:
        result.quality_flags.append(f"Deep nesting / indentation (approx depth {max_indent})")

    magic_count = len(_MAGIC_NUMBER.findall(content))
    if magic_count >= 8:
        result.quality_flags.append(f"Many numeric literals ({magic_count}); consider named constants")

    # Long lines
    long_lines = sum(1 for ln in lines if len(ln) > 140)
    if long_lines >= 5:
        result.quality_flags.append(f"Several very long lines ({long_lines})")


def _scan_naming(content: str, lang: str, result: FileStaticResult) -> None:
    if lang != "python":
        # light heuristic for camelCase vs snake_case mix
        has_snake = bool(re.search(r"\b[a-z]+_[a-z]+\b", content))
        has_camel = bool(re.search(r"\b[a-z]+[A-Z][a-zA-Z]+\b", content))
        if has_snake and has_camel:
            result.naming_flags.append("Mixed naming styles (snake_case and camelCase) in same file")
        return

    try:
        tree = ast.parse(content)
    except SyntaxError:
        result.naming_flags.append("Syntax errors prevent naming analysis")
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if _SHORT_NAME.match(node.id) and not node.id.startswith("_"):
                result.naming_flags.append(f"Very short identifier '{node.id}'")
                break


def _scan_design(content: str, lang: str, result: FileStaticResult) -> None:
    if lang == "python":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        long_funcs = [f.name for f in funcs if f.end_lineno and f.lineno and (f.end_lineno - f.lineno) > 80]
        if long_funcs:
            result.design_flags.append(f"Very long functions (>{80} lines): {', '.join(long_funcs[:3])}")
        if len(funcs) > 25 and len(classes) < 2:
            result.design_flags.append("Many functions in one module; consider splitting")
    else:
        # line-based function block heuristic
        func_lines = 0
        for ln in content.splitlines():
            if re.match(r"^\s*(function\s+\w+|def\s+\w+|func\s+\w+|void\s+\w+\s*\()", ln):
                func_lines += 1
        if func_lines > 30:
            result.design_flags.append("High function count in file; check single responsibility")


def _scan_docs(content: str, lang: str, result: FileStaticResult) -> None:
    if lang == "python":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not funcs:
            return
        with_doc = sum(1 for f in funcs if ast.get_docstring(f))
        ratio = with_doc / len(funcs)
        if ratio < 0.25 and len(funcs) >= 4:
            result.doc_flags.append(f"Low docstring coverage on functions ({ratio:.0%})")
    elif lang in ("javascript", "typescript", "java", "go"):
        comment_ratio = content.count("//") + content.count("/*") + content.count("*")
        if comment_ratio < 3 and len(content) > 2000:
            result.doc_flags.append("Sparse comments/JSDoc relative to file size")


def _analyze_markdown(path: str, content: str, result: FileStaticResult) -> None:
    if "installation" not in content.lower() and "usage" not in content.lower() and len(content) > 500:
        result.doc_flags.append("README may lack clear Installation/Usage sections")
    if content.count("#") < 2:
        result.doc_flags.append("README structure looks minimal (few headings)")


def _scan_deps_config(path: str, content: str, lang: str, result: FileStaticResult) -> None:
    if re.search(r"os\.environ|getenv\s*\(|process\.env", content):
        result.dep_flags.append("Uses environment variables for configuration")
    if re.search(r"https?://[^\s\"']+", content) and "example.com" not in content.lower():
        result.dep_flags.append("Possible hardcoded host or URL")
    if lang == "yaml" and "password" in content.lower() and "secret" in content.lower():
        result.security_flags.append("YAML may reference secrets; ensure not committed in plain text")


def _scan_tests(path: str, content: str, lang: str, result: FileStaticResult) -> None:
    p = path.replace("\\", "/").lower()
    if "test" in PurePosixPath(p).name or "/test" in p or "/tests" in p:
        result.test_flags.append("Test-related file path")


def _scan_performance(content: str, lang: str, result: FileStaticResult) -> None:
    if re.search(r"for\s+.*:\s*\n\s*for\s+", content):
        result.perf_flags.append("Nested loops; verify complexity")
    if re.search(r"time\.sleep\s*\(\s*[1-9]", content) or re.search(r"Thread\.sleep", content):
        result.perf_flags.append("Blocking sleep in code path")
    if content.count(".append(") > 40 and "while" in content:
        result.perf_flags.append("Possible repeated list append in loop; consider preallocation")


def _analyze_python_ast(content: str, result: FileStaticResult) -> None:
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        result.quality_flags.append(f"Python syntax error: {e.msg}")
        return

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)

    unused = [m for m in set(imports) if m and m not in used_names and m != "__future__"]
    if len(unused) >= 2:
        result.quality_flags.append(f"Possibly unused imports: {', '.join(sorted(unused)[:8])}")

