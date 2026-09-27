"""Web server entry point: ``python -m nwn_translator.web`` or ``nwn-translate-web``."""

import os
import sys

from dotenv import load_dotenv

#: Binds that make the process a local single-user run, the only one in which
#: ``/api/config`` hands the ``.env`` API key to the UI. Any other bind (``0.0.0.0``,
#: docker, a deployed instance) keeps the key on the server.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    """Loads ``.env`` and serves the app with uvicorn.

    ``NWN_WEB_HOST`` (default ``127.0.0.1``), ``NWN_WEB_PORT`` (``8000``) and
    ``NWN_WEB_RELOAD`` configure the server; the bind address decides local mode
    once, not per request.

    Raises:
        SystemExit: If uvicorn is not installed.
    """
    load_dotenv()
    try:
        import uvicorn
    except ImportError as e:
        print(
            "Uvicorn не установлен. Установите зависимости веб-слоя:\n" '  pip install -e ".[web]"',
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    host = os.environ.get("NWN_WEB_HOST", "127.0.0.1")
    if host in _LOOPBACK_HOSTS:
        os.environ["NWN_WEB_LOCAL_MODE"] = "1"
    uvicorn.run(
        "nwn_translator.web.app:create_app",
        factory=True,
        host=host,
        port=int(os.environ.get("NWN_WEB_PORT", "8000")),
        reload=os.environ.get("NWN_WEB_RELOAD", "").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    main()
