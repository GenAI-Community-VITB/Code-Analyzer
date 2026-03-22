"""FastAPI entrypoint: shallow-clone analysis only."""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from app.exceptions import (
    AnalyzerError,
    CloneFailedError,
    EmptyRepositoryError,
    InvalidRepositoryURLError,
    RepositoryInaccessibleError,
)
from app.models import ErrorResponse
from app.services.clone_analyze_service import analyze_repository_via_clone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.main")

app = FastAPI(
    title="Repository Code Analyzer",
    description="Shallow git clone, hybrid static + Gemini analysis, report, cleanup.",
    version="2.0.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/analyze")
async def analyze(
    repo_url: str = Query(..., min_length=8, description="https://github.com/owner/repository"),
):
    logger.info("Analyze (clone) repo_url=%s", repo_url)
    try:
        result = await analyze_repository_via_clone(repo_url)
        logger.info("Done: %s", result.repository)
        return result.model_dump()
    except InvalidRepositoryURLError as e:
        logger.warning("Invalid URL: %s", e.message)
        return _err(400, e.message, code=e.code)
    except RepositoryInaccessibleError as e:
        logger.warning("Inaccessible: %s", e.message)
        return _err(403, e.message, code=e.code)
    except CloneFailedError as e:
        logger.error("Clone failed: %s", e.message)
        return _err(502, e.message, code=e.code)
    except EmptyRepositoryError as e:
        logger.warning("Empty: %s", e.message)
        return _err(404, e.message, code=e.code)
    except AnalyzerError as e:
        logger.error("Analyzer: %s", e.message)
        return _err(502, e.message, code=e.code)
    except Exception as e:
        logger.exception("Unexpected failure")
        return _err(500, "Internal server error", code="internal_error", detail=str(e))


def _err(status: int, message: str, code: str | None = None, detail: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=message, code=code, detail=detail).model_dump(exclude_none=True)
    return JSONResponse(status_code=status, content=body)


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
