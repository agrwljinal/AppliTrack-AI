import json
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

PLATFORMS = ("LinkedIn", "Unstop", "Indeed", "Glassdoor", "Wellfound")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "applitrack_config.json"
console = Console()


def _select_platforms() -> list[str]:
    selected: set[str] = set()

    while True:
        table = Table(title="Target Job Platforms")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Platform")
        table.add_column("Selection", justify="center")

        for index, platform in enumerate(PLATFORMS, start=1):
            state = "[green]Selected[/green]" if platform in selected else "[dim]Not selected[/dim]"
            table.add_row(str(index), platform, state)

        console.print(table)
        choice = Prompt.ask("Toggle a number, type 'all', or press Enter when done", default="done")

        if choice.casefold() == "done":
            return [platform for platform in PLATFORMS if platform in selected]
        if choice.casefold() == "all":
            selected = set(PLATFORMS)
            continue
        if choice.isdecimal() and 1 <= int(choice) <= len(PLATFORMS):
            platform = PLATFORMS[int(choice) - 1]
            if platform in selected:
                selected.remove(platform)
            else:
                selected.add(platform)
            continue

        console.print("[yellow]Choose a listed number, 'all', or Enter to finish.[/yellow]")


def register_onboarding_command(app: typer.Typer) -> None:
    @app.command("configure")
    def configure() -> None:
        """Choose job platforms and privacy preferences."""
        console.print("[bold cyan]AppliTrack AI Onboarding[/bold cyan]")
        selected_platforms = _select_platforms()
        email_updates_parsing = Confirm.ask(
            "Enable Email Updates Parsing?",
            default=False,
        )

        preferences = {
            "selected_platforms": selected_platforms,
            "email_updates_parsing": email_updates_parsing,
        }
        CONFIG_PATH.write_text(
            json.dumps(preferences, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Preferences saved to {CONFIG_PATH.name}.[/green]")