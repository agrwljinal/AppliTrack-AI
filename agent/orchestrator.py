import json
import logging
from pathlib import Path
from typing import Any

from services.ai_classifier import classify_application
from services.sheet_service import update_or_append_application

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "applitrack_config.json"
logger = logging.getLogger(__name__)


def scan_portal(platform: str) -> list[str]:
    logger.warning("Portal scanning for %s is not implemented yet.", platform)
    return []


def scan_email_updates() -> list[str]:
    logger.warning("Email updates parsing is not implemented yet.")
    return []


def _load_preferences() -> dict[str, Any]:
    try:
        preferences = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("No preferences found; run 'python main.py configure' first.")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read AppliTrack preferences: %s", exc)
        return {}

    if not isinstance(preferences, dict):
        logger.warning("AppliTrack preferences must contain a JSON object.")
        return {}
    return preferences


def run_cycle() -> dict[str, int]:
    preferences = _load_preferences()
    selected_platforms = preferences.get("selected_platforms", [])
    if not isinstance(selected_platforms, list):
        logger.warning("selected_platforms must be a list; no portals will be scanned.")
        selected_platforms = []

    platforms = [
        platform.strip()
        for platform in selected_platforms
        if isinstance(platform, str) and platform.strip()
    ]
    updates: list[tuple[str, str]] = []

    for platform in platforms:
        updates.extend((platform, raw_text) for raw_text in scan_portal(platform))

    email_enabled = preferences.get("email_updates_parsing") is True
    if email_enabled:
        updates.extend(("Email", raw_text) for raw_text in scan_email_updates())

    classified_count = 0
    sheet_update_count = 0
    for platform, raw_text in updates:
        if not raw_text.strip():
            continue
        try:
            application = classify_application(raw_text, platform)
            classified_count += 1
            if update_or_append_application(application):
                sheet_update_count += 1
        except Exception:
            logger.exception("Could not process an update from %s.", platform)

    return {
        "platforms_scanned": len(platforms),
        "email_scanner_runs": int(email_enabled),
        "raw_updates": len(updates),
        "classified": classified_count,
        "sheet_updates": sheet_update_count,
    }