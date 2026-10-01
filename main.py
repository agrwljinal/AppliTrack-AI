import typer
from fastapi import FastAPI
from rich.console import Console
from rich.panel import Panel

cli = typer.Typer(no_args_is_help=True)
api = FastAPI(title="AppliTrack AI")
console = Console()


@cli.callback()
def cli_callback() -> None:
    """Initialize the command group for future CLI commands."""


def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]AppliTrack AI[/bold cyan]\n"
            "Your privacy-focused job application tracker",
            border_style="cyan",
        )
    )
    cli()


if __name__ == "__main__":
    main()