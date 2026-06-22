"""Entry point: ``python -m steward`` or the ``steward`` console script."""
from __future__ import annotations

import uvicorn

from steward.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "steward.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
