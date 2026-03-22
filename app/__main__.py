"""Allow: python -m app (delegates to CLI clone pipeline)."""

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
