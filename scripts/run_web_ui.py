#!/usr/bin/env python3
"""
Запуск веб-интерфейса одной командой: FastAPI (порт 8000) + Vite dev (порт 5173).

Требования:
  pip install -e ".[web]"
  cd frontend && npm install

Запуск из корня репозитория:
  python scripts/run_web_ui.py

Переменные окружения (или .env в корне): NWN_TRANSLATE_API_KEY, NWN_WEB_HOST, NWN_WEB_PORT.
Откройте в браузере: http://localhost:5173
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def _npm_executable() -> str | None:
    for name in ("npm", "npm.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _check_node_modules() -> bool:
    return (FRONTEND / "node_modules").is_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск API + фронтенда NWN Translator")
    parser.add_argument(
        "--host",
        default=os.environ.get("NWN_WEB_HOST", "127.0.0.1"),
        help="Хост API (по умолчанию из NWN_WEB_HOST или 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NWN_WEB_PORT", "8000")),
        help="Порт API (по умолчанию из NWN_WEB_PORT или 8000)",
    )
    parser.add_argument(
        "--no-browser-hint",
        action="store_true",
        help="Не печатать подсказку с URL",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    _load_dotenv()

    if not _check_node_modules():
        print(
            "Не найден frontend/node_modules.\n"
            "Выполните:\n"
            f"  cd {FRONTEND}\n"
            "  npm install",
            file=sys.stderr,
        )
        return 1

    npm = _npm_executable()
    if not npm:
        print("Не найден npm. Установите Node.js: https://nodejs.org/", file=sys.stderr)
        return 1

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print(
            'Не установлен uvicorn. Выполните: pip install -e ".[web]"',
            file=sys.stderr,
        )
        return 1

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "nwn_translator.web.app:create_app",
        "--factory",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]

    print(f"[backend] Запуск API http://{args.host}:{args.port}")
    backend = subprocess.Popen(
        backend_cmd,
        cwd=str(ROOT),
        env=os.environ.copy(),
    )

    print("[frontend] Запуск Vite (npm run dev)…")
    frontend = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(FRONTEND),
        env=os.environ.copy(),
        shell=False,
    )

    procs: list[subprocess.Popen] = [backend, frontend]

    def stop_all() -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.1)
        for p in procs:
            if p.poll() is None:
                p.kill()

    def _on_signal(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
        stop_all()
        sys.exit(128 + signum if signum else 0)

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    if not args.no_browser_hint:
        print()
        print("  Откройте в браузере:  http://localhost:5173")
        print("  Остановка: Ctrl+C")
        print()

    exit_code = 0
    try:
        while True:
            if backend.poll() is not None:
                print("[backend] Процесс завершился.", file=sys.stderr)
                exit_code = 1 if backend.returncode is None else int(backend.returncode)
                break
            if frontend.poll() is not None:
                print("[frontend] Процесс завершился.", file=sys.stderr)
                exit_code = 1 if frontend.returncode is None else int(frontend.returncode)
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nОстановка…")
    finally:
        stop_all()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
