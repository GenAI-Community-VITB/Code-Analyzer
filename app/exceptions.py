"""Domain-specific exceptions for the analyzer."""


class AnalyzerError(Exception):
    """Base error for analyzer operations."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "analyzer_error"


class InvalidRepositoryURLError(AnalyzerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid_repository_url")


class RepositoryInaccessibleError(AnalyzerError):
    def __init__(self, message: str = "Repository is private or inaccessible") -> None:
        super().__init__(message, code="repository_inaccessible")


class EmptyRepositoryError(AnalyzerError):
    def __init__(self, message: str = "Repository has no analyzable files") -> None:
        super().__init__(message, code="empty_repository")


class LLMConfigurationError(AnalyzerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="llm_not_configured")


class CloneFailedError(AnalyzerError):
    """Git clone or local workspace failure (non-auth)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="clone_failed")
