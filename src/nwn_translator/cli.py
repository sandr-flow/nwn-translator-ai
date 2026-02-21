"""Command-line interface for NWN Modules Translator.

This module provides the CLI for translating Neverwinter Nights modules.
"""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import Progress

from .config import TranslationConfig, create_output_path, STANDARD_TOKENS
from .main import translate_module
from .ai_providers import ProviderFactory, create_provider

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
@click.version_option(version="0.1.0", prog_name="nwn-translate")
@click.pass_context
def cli(ctx):
    """NWN Modules Translator - AI-powered translation for Neverwinter Nights modules.

    Translate .mod files from any language to any language using AI.

    Example:
        nwn-translate module.mod --api-key YOUR_KEY --lang spanish
    """
    if ctx.invoked_subcommand is None:
        console.print(Panel.fit(
            "[bold cyan]NWN Modules Translator[/bold cyan]\n\n"
            "AI-powered translation for Neverwinter Nights modules.\n\n"
            "[dim]Use --help for usage information[/dim]",
            title="Welcome",
        ))
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
    help="API key for the AI provider",
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
    "--provider",
    "-p",
    default="openrouter",
    type=click.Choice(["grok", "openai", "gemini", "mistral", "openrouter"], case_sensitive=False),
    help="AI provider to use (default: openrouter)",
)
@click.option(
    "--model",
    "-m",
    help="Model to use (provider default if not specified)",
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
def translate(
    input_file: Path,
    api_key: str,
    target_lang: str,
    source_lang: str,
    provider: str,
    model: str,
    output_file: Path,
    temp_dir: Path,
    log_file: Path,
    skip_cleanup: bool,
    verbose: bool,
    quiet: bool,
    no_tokens: bool,
    context: bool,
):
    """Translate a NWN module file.

    INPUT_FILE: Path to the .mod file to translate
    """
    setup_logging(verbose, quiet)

    # Validate API key
    if not api_key:
        console.print("[bold red]Error:[/bold red] API key is required!")
        console.print("Set NWN_TRANSLATE_API_KEY environment variable or use --api-key")
        sys.exit(1)

    # Generate output path if not specified
    if not output_file:
        output_file = create_output_path(input_file, target_lang)

    # Generate log file path if not specified
    if not log_file:
        log_file = output_file.with_name(output_file.stem + "_log.jsonl")

    # Create configuration
    config_kwargs = {
        "api_key": api_key,
        "provider": provider,
        "model": model,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "input_file": input_file,
        "output_file": output_file,
        "translation_log": log_file,
        "skip_cleanup": skip_cleanup,
        "preserve_tokens": not no_tokens,
        "use_context": context,
        "verbose": verbose,
        "quiet": quiet,
    }
    
    if temp_dir is not None:
        config_kwargs["temp_dir"] = temp_dir
        
    config = TranslationConfig(**config_kwargs)

    # Display translation info
    if not quiet:
        console.print(f"\n[bold]Translation Configuration:[/bold]")
        console.print(f"  Input: {input_file}")
        console.print(f"  Output: {output_file}")
        console.print(f"  Provider: {provider}" + (f" ({model})" if model else ""))
        console.print(f"  Target: {target_lang}")
        console.print(f"  Log file: {log_file}")
        console.print(f"  Preserve tokens: {not no_tokens}\n")

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
    "--provider",
    "-p",
    default="openrouter",
    type=click.Choice(["grok", "openai", "gemini", "mistral", "openrouter"], case_sensitive=False),
    help="AI provider",
)
@click.option(
    "--model",
    "-m",
    help="Model identifier",
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
    required=True,
    help="API key",
)
def test(
    provider: str,
    model: str,
    text: str,
    target_lang: str,
    api_key: str,
):
    """Test the AI provider with a simple translation.

    Useful for verifying API key and provider configuration.
    """
    setup_logging()

    if not text:
        text = "Hello, welcome to my module!"

    console.print(f"[bold]Testing {provider} provider[/bold]")
    console.print(f"Text: {text}")
    console.print(f"Target: {target_lang}\n")

    try:
        provider_instance = create_provider(provider, api_key, model)
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


@cli.command()
def providers():
    """List available AI providers."""
    setup_logging()

    console.print("[bold]Available AI Providers:[/bold]\n")

    providers_info = {
        "openrouter": {
            "name": "OpenRouter",
            "default_model": "openai/gpt-oss-120b",
            "description": "Gateway to 100+ models (Claude, GPT, Gemini, DeepSeek…)",
        },
        "grok": {
            "name": "Grok (xAI)",
            "default_model": "grok-2",
            "description": "Fast and affordable",
        },
        "openai": {
            "name": "OpenAI",
            "default_model": "gpt-4o-mini",
            "description": "GPT-4 and GPT-4o models",
        },
        "gemini": {
            "name": "Gemini (Google)",
            "default_model": "gemini-pro",
            "description": "Google's Gemini models",
        },
        "mistral": {
            "name": "Mistral AI",
            "default_model": "mistral-medium",
            "description": "Mistral AI models",
        },
    }

    for provider_id, info in providers_info.items():
        console.print(f"  [cyan]{provider_id}[/cyan]")
        console.print(f"    Name: {info['name']}")
        console.print(f"    Default: {info['default_model']}")
        console.print(f"    {info['description']}\n")


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
