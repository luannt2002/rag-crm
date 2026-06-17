"""RAGbot API entrypoint.

Ref: docs/application/PLAN_01_WORKSPACE_BOOTSTRAP.md §main.py
     PLAN_10 §lifespan.py / §app.py.
"""

from __future__ import annotations

import uvloop
import uvicorn

from ragbot.config import get_settings
from ragbot.config.logging import setup_logging
def main() -> None:
    """Run uvicorn with uvloop + httptools (production-grade defaults)."""
    uvloop.install()
    settings = get_settings()

    setup_logging(
        level=settings.observability.log_level,
        json=settings.observability.log_format == "json",
    )

    uvicorn.run(
        "ragbot.interfaces.http.app:app",
        host=settings.app.host,
        port=settings.app.port,
        loop="uvloop",
        http="httptools",
        log_config=None,  # structlog handles logging
        access_log=False,  # access via middleware
        reload=settings.app.debug,
    )


if __name__ == "__main__":
    main()
