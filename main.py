import logging
from contextlib import asynccontextmanager
from typing import Any

import typer
from fastapi import FastAPI
from rich.console import Console
from rich.panel import Panel

from api.routes import router
from agent.orchestrator import run_cycle
from cli.onboarding import register_onboarding_command
from services.gmail_service import start_background_poller

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if start_background_poller():
        logger.info("Gmail ingestion poller started.")
    yield


cli = typer.Typer(no_args_is_help=True)
app = FastAPI(title="AppliTrack AI", lifespan=lifespan)
app.include_router(router)
api = app
console = Console()
register_onboarding_command(cli)


@app.get("/")
def read_root() -> dict[str, Any]:
    return {
        "status": "online",
        "service": "AppliTrack AI API",
        "documentation": "/docs",
        "health_check": "/health",
    }


@cli.callback()
def cli_callback() -> None:
    """Initialize the command group for future CLI commands."""


@cli.command()
def run() -> None:
    """Run one AppliTrack AI check cycle."""
    results = run_cycle()
    console.print("[bold green]Check cycle complete[/bold green]")
    console.print(
        f"Portals scanned: {results['platforms_scanned']} | "
        f"Raw updates: {results['raw_updates']} | "
        f"Classified: {results['classified']} | "
        f"Sheet updates: {results['sheet_updates']}"
    )


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