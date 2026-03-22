"""CLI entrypoint for clone-based repository analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.exceptions import (
    AnalyzerError,
    CloneFailedError,
    EmptyRepositoryError,
    InvalidRepositoryURLError,
    RepositoryInaccessibleError,
)
from app.services.clone_analyze_service import analyze_repository_via_clone


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clone a GitHub repo (shallow), run hybrid analysis, write report, delete clone.",
    )
    parser.add_argument(
        "repo_url",
        help="https://github.com/owner/repository",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print result as JSON to stdout",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    logger = logging.getLogger("app.cli")

    async def _run() -> None:
        result = await analyze_repository_via_clone(args.repo_url)
        if args.json:
            print(json.dumps(result.model_dump(), indent=2, default=str))
        else:
            logger.info("Overall score: %s", result.overall_score)
            logger.info("Report: %s", result.report_file)

    try:
        asyncio.run(_run())
        return 0
    except InvalidRepositoryURLError as e:
        logging.error("%s", e.message)
        return 2
    except RepositoryInaccessibleError as e:
        logging.error("%s", e.message)
        return 3
    except CloneFailedError as e:
        logging.error("%s", e.message)
        return 4
    except EmptyRepositoryError as e:
        logging.error("%s", e.message)
        return 5
    except AnalyzerError as e:
        logging.error("%s", e.message)
        return 1
    except KeyboardInterrupt:
        logging.warning("Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
