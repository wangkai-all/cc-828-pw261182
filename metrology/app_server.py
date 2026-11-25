"""Dev server entry point: ``python -m metrology.app_server``."""
from __future__ import annotations

import os

from .app import app


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5077"))
    app.run(host=host, port=port, debug=bool(os.environ.get("METROLOGY_DEBUG")), use_reloader=False)


if __name__ == "__main__":
    main()
