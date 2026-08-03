"""Enable ``python -m nur`` as an alias for the ``nur`` console script."""

from __future__ import annotations

from nur.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())  # pragma: no cover - exercised via subprocess only
