"""Run the web server: ``python -m nwn_translator.web`` or ``nwn-translate-web``."""

import os
import sys

from dotenv import load_dotenv

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _enable_local_mode_if_loopback(host: str) -> bool:
    """Mark this process as a local single-user run when bound to loopback.

    Only in local mode does ``/api/config`` hand the server's ``.env`` API key to
    the UI for autofill convenience. Any non-loopback bind (``0.0.0.0``, docker,
    a deployed instance) leaves the flag unset, so the key never leaves the
    server. The decision is made once from the bind address, not per request.
    """
    if host in _LOOPBACK_HOSTS:
        os.environ["NWN_WEB_LOCAL_MODE"] = "1"
        return True
    return False


def main() -> None:
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
    port = int(os.environ.get("NWN_WEB_PORT", "8000"))
    reload = os.environ.get("NWN_WEB_RELOAD", "").lower() in ("1", "true", "yes")

    _enable_local_mode_if_loopback(host)

    uvicorn.run(
        "nwn_translator.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
