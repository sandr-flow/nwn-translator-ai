"""Run the web server: ``python -m nwn_translator.web`` or ``nwn-translate-web``."""

import os
import sys


def main() -> None:
    try:
        import uvicorn
    except ImportError as e:
        print(
            "Uvicorn не установлен. Установите зависимости веб-слоя:\n"
            "  pip install -e \".[web]\"",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    host = os.environ.get("NWN_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("NWN_WEB_PORT", "8000"))
    reload = os.environ.get("NWN_WEB_RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "nwn_translator.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
