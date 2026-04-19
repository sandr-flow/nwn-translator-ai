"""Command-line interface for NWN Modules Translator.

This module provides the CLI for translating Neverwinter Nights modules.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

from .ai_providers import OpenRouterProvider, create_provider
from .config import (
    OPENROUTER_REASONING_EFFORT_VALUES,
    TranslationConfig,
    STANDARD_TOKENS,
)
from .main import translate_module

# Setup console and logging
console = Console()


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """Setup logging configuration.

    Args:
        verbose: Enable verbose logging
        quiet: Suppress non-error output
    """
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False)],
    )


@click.group(invoke_without_command=True)
@click.version_option(
    version=None, prog_name="nwn-translate", package_name="nwn-modules-translator"
)
@click.pass_context
def cli(ctx):
    """NWN Modules Translator - AI-powered translation for Neverwinter Nights modules.

    Translate .mod files from any language to any language using AI.

    Example:
        nwn-translate module.mod --lang spanish

    Put ``NWN_TRANSLATE_API_KEY`` in a ``.env`` file (or the environment); ``--api-key`` is optional.
    """
    if ctx.invoked_subcommand is None:
        console.print(
            Panel.fit(
                "[bold cyan]NWN Modules Translator[/bold cyan]\n\n"
                "AI-powered translation for Neverwinter Nights modules.\n\n"
                "[dim]Use --help for usage information[/dim]",
                title="Welcome",
            )
        )
        ctx.invoke(translate)


@cli.command()
@click.argument(
    "input_file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--api-key",
    "-k",
    envvar="NWN_TRANSLATE_API_KEY",
    help="OpenRouter API key (optional if NWN_TRANSLATE_API_KEY is set, e.g. in .env)",
)
@click.option(
    "--lang",
    "-l",
    "target_lang",
    required=True,
    help="Target language (e.g., spanish, french, german, russian)",
)
@click.option(
    "--source-lang",
    "-s",
    default="auto",
    help="Source language (default: auto-detect)",
)
@click.option(
    "--model",
    "-m",
    help="OpenRouter model slug (default from config / OpenRouterProvider)",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(path_type=Path),
    help="Output file path (auto-generated if not specified)",
)
@click.option(
    "--temp-dir",
    type=click.Path(path_type=Path),
    help="Temporary directory for extraction",
)
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    help="Path to save translation log (.jsonl). Auto-generated if not specified.",
)
@click.option(
    "--skip-cleanup",
    is_flag=True,
    help="Keep temporary files for debugging",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress non-error output",
)
@click.option(
    "--no-tokens",
    is_flag=True,
    help="Do not preserve game tokens during translation",
)
@click.option(
    "--context/--no-context",
    default=True,
    help="Enable/disable contextual full-dialog translation (default: enabled)",
)
@click.option(
    "--max-concurrent",
    type=int,
    default=None,
    help=(
        "Override NWN_TRANSLATE_MAX_CONCURRENT: parallel OpenRouter requests (async). "
        "If omitted, uses .env / environment (default 12 when unset)."
    ),
)
@click.option(
    "--tlk",
    type=click.Path(exists=True, path_type=Path),
    help="Path to dialog.tlk for resolving StrRef-based names (auto-detected if not specified)",
)
@click.option(
    "--gender",
    "-g",
    "player_gender",
    type=click.Choice(["male", "female"]),
    default="male",
    show_default=True,
    help="Player character gender (affects grammatical forms in translation)",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(sorted(OPENROUTER_REASONING_EFFORT_VALUES)),
    default=None,
    help=(
        "OpenRouter reasoning.effort for supported models; omitted by default. "
        "If the API rejects it, the client retries without reasoning."
    ),
)
def translate(
    input_file: Path,
    api_key: str,
    target_lang: str,
    source_lang: str,
    model: str,
    output_file: Path,
    temp_dir: Path,
    log_file: Path,
    skip_cleanup: bool,
    verbose: bool,
    quiet: bool,
    no_tokens: bool,
    context: bool,
    max_concurrent: Optional[int],
    tlk: Path,
    player_gender: str,
    reasoning_effort: Optional[str],
):
    """Translate a NWN module file.

    INPUT_FILE: Path to the .mod file to translate
    """
    setup_logging(verbose, quiet)

    # Generate workspace and paths if output_file is not specified
    if not output_file:
        from .config import lang_suffix, sanitized_mod_stem

        safe_stem = sanitized_mod_stem(input_file.stem)
        suf = lang_suffix(target_lang)
        workspace_dir = Path("workspace") / f"{safe_stem}{suf}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        output_file = workspace_dir / f"{safe_stem}{suf}.mod"

        if not log_file:
            log_file = workspace_dir / "translation_log.jsonl"

        if temp_dir is None:
            temp_dir = workspace_dir / "temp"
    else:
        # Generate log file path if not specified, based on custom output path
        if not log_file:
            log_file = output_file.with_name(output_file.stem + "_log.jsonl")

    # Create configuration (omit empty api_key so TranslationConfig reads .env / os.environ)
    config_kwargs = {
        "model": model,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "input_file": input_file,
        "output_file": output_file,
        "translation_log": log_file,
        "skip_cleanup": skip_cleanup,
        "preserve_tokens": not no_tokens,
        "use_context": context,
        "tlk_file": tlk,
        "player_gender": player_gender,
        "verbose": verbose,
        "quiet": quiet,
    }

    if temp_dir is not None:
        config_kwargs["temp_dir"] = temp_dir
    if api_key:
        config_kwargs["api_key"] = api_key
    if max_concurrent is not None:
        config_kwargs["max_concurrent_requests"] = max(1, max_concurrent)
    if reasoning_effort:
        config_kwargs["reasoning_effort"] = reasoning_effort

    config = TranslationConfig(**config_kwargs)

    # Display translation info
    if not quiet:
        console.print(f"\n[bold]Translation Configuration:[/bold]")
        console.print(f"  Input: {input_file}")
        console.print(f"  Output: {output_file}")
        console.print(f"  OpenRouter model: {model or OpenRouterProvider.DEFAULT_MODEL}")
        console.print(f"  Target: {target_lang}")
        console.print(f"  Log file: {log_file}")
        console.print(f"  Preserve tokens: {not no_tokens}")
        console.print(
            f"  Max concurrent API requests: {config.max_concurrent_requests} "
            f"(NWN_TRANSLATE_MAX_CONCURRENT / --max-concurrent)"
        )
        if config.reasoning_effort:
            console.print(f"  Reasoning effort: {config.reasoning_effort}")
        console.print()

    try:
        # Perform translation
        result_path = translate_module(config)

        # Display results
        if not quiet:
            console.print(f"\n[bold green]Success![/bold green]")
            console.print(f"Translated module: [cyan]{result_path}[/cyan]")

    except Exception as e:
        console.print(f"\n[bold red]Translation failed:[/bold red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command()
@click.option(
    "--model",
    "-m",
    help="OpenRouter model slug",
)
@click.option(
    "--text",
    "-t",
    help="Text to translate",
)
@click.option(
    "--lang",
    "-l",
    "target_lang",
    required=True,
    help="Target language",
)
@click.option(
    "--api-key",
    "-k",
    envvar="NWN_TRANSLATE_API_KEY",
    help="OpenRouter API key (optional if NWN_TRANSLATE_API_KEY is set, e.g. in .env)",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(sorted(OPENROUTER_REASONING_EFFORT_VALUES)),
    default=None,
    help="OpenRouter reasoning.effort (optional).",
)
def test(
    model: str,
    text: str,
    target_lang: str,
    api_key: str,
    reasoning_effort: Optional[str],
):
    """Test OpenRouter with a simple translation.

    Useful for verifying API key and model configuration.
    """
    setup_logging()

    if not text:
        text = "Hello, welcome to my module!"

    console.print("[bold]Testing OpenRouter[/bold]")
    console.print(f"Text: {text}")
    console.print(f"Target: {target_lang}\n")

    try:
        key = api_key or os.environ.get("NWN_TRANSLATE_API_KEY", "")
        if not key.strip():
            console.print(
                "[bold red]Error:[/bold red] Set NWN_TRANSLATE_API_KEY in .env " "or pass --api-key"
            )
            sys.exit(1)
        provider_instance = create_provider(key, model, reasoning_effort=reasoning_effort)
        result = provider_instance.translate(text, "english", target_lang)

        if result.success:
            console.print(f"[bold green]Translation:[/bold green] {result.translated}")
        else:
            console.print(f"[bold red]Error:[/bold red] {result.error}")
            sys.exit(1)

    except Exception as e:
        console.print(f"[bold red]Test failed:[/bold red] {e}")
        sys.exit(1)


@cli.command()
def tokens():
    """List all standard NWN game tokens that are preserved."""
    setup_logging()

    console.print("[bold]Standard NWN Game Tokens:[/bold]\n")

    for token in sorted(STANDARD_TOKENS):
        console.print(f"  {token}")

    console.print("\n[dim]Custom tokens like <CustomToken:123> are also preserved.[/dim]")


@cli.command("web")
@click.option(
    "--host",
    "host",
    default="127.0.0.1",
    envvar="NWN_WEB_HOST",
    show_default=True,
    help="Server bind address",
)
@click.option(
    "--port",
    "port",
    default=8000,
    envvar="NWN_WEB_PORT",
    show_default=True,
    type=int,
    help="Server port",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Auto-reload on code changes (development)",
)
def web_server(host: str, port: int, reload: bool):
    """Start the web interface (FastAPI + SPA API).

    Requires extra dependencies: pip install -e ".[web]"
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] web dependencies are not installed.\n"
            'Run: [cyan]pip install -e ".[web]"[/cyan]'
        )
        sys.exit(1)

    console.print(
        f"[bold]Web server[/bold] [cyan]http://{host}:{port}[/cyan]\n"
        "[dim]API: /api/…  Frontend: cd frontend && npm run dev[/dim]"
    )
    uvicorn.run(
        "nwn_translator.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@cli.command("providers")
def list_providers():
    """Show OpenRouter default model and a short list of popular model slugs."""
    setup_logging()

    console.print("[bold]OpenRouter[/bold] (https://openrouter.ai)\n")
    console.print(f"  Default model: [cyan]{OpenRouterProvider.DEFAULT_MODEL}[/cyan]\n")
    console.print("[dim]Popular model slugs (pass with --model):[/dim]\n")
    for slug in OpenRouterProvider.POPULAR_MODELS:
        console.print(f"  • {slug}")
    console.print("\n[dim]See https://openrouter.ai/models for the full catalog.[/dim]")


def main():
    """Main entry point for the CLI."""
    load_dotenv()
    cli()


if __name__ == "__main__":
    main()
