import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Literal

import gspread
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CREDENTIALS = PROJECT_ROOT / "credentials.json"
WORKSHEET_NAME = "Applications"
HEADERS = ["company_name", "role", "platform_used", "status", "date_updated"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")


class JobApplication(BaseModel):
    company_name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    platform_used: str = Field(min_length=1)
    status: Literal["Selected", "Rejected", "Pending"]
    date_updated: date = Field(default_factory=date.today)


def _create_client() -> gspread.Client | None:
    credential_value = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")

    if credential_value:
        credential_path = Path(credential_value).expanduser()
        if not credential_path.is_absolute():
            credential_path = PROJECT_ROOT / credential_path

        if credential_path.is_file():
            try:
                return gspread.service_account(filename=str(credential_path), scopes=SCOPES)
            except Exception as exc:
                logger.warning("Could not load Google Sheets credentials: %s", exc)
                return None

        try:
            credentials_info = json.loads(credential_value)
            return gspread.service_account_from_dict(credentials_info, scopes=SCOPES)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "GOOGLE_SHEETS_CREDENTIALS_JSON must be a credential file path or valid JSON: %s",
                exc,
            )
            return None
        except Exception as exc:
            logger.warning("Could not initialize Google Sheets credentials: %s", exc)
            return None

    if DEFAULT_CREDENTIALS.is_file():
        try:
            return gspread.service_account(filename=str(DEFAULT_CREDENTIALS), scopes=SCOPES)
        except Exception as exc:
            logger.warning("Could not load credentials.json: %s", exc)
            return None

    logger.warning(
        "Google Sheets credentials are not configured; set "
        "GOOGLE_SHEETS_CREDENTIALS_JSON or add credentials.json."
    )
    return None


def _get_worksheet() -> gspread.Worksheet | None:
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        logger.warning("Google Sheet is not configured; set GOOGLE_SHEET_ID.")
        return None

    client = _create_client()
    if client is None:
        return None

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=WORKSHEET_NAME,
                rows=1000,
                cols=len(HEADERS),
            )

        if not worksheet.row_values(1):
            worksheet.update(values=[HEADERS], range_name="A1:E1")
        return worksheet
    except Exception as exc:
        logger.warning("Could not open the AppliTrack Google Sheet: %s", exc)
        return None


def update_or_append_application(app_data: JobApplication) -> bool:
    worksheet = _get_worksheet()
    if worksheet is None:
        return False

    row = [
        app_data.company_name,
        app_data.role,
        app_data.platform_used,
        app_data.status,
        app_data.date_updated.isoformat(),
    ]

    try:
        existing_rows = worksheet.get_all_values()
        for row_number, existing_row in enumerate(existing_rows[1:], start=2):
            existing_key = tuple(existing_row[:3])
            if existing_key == tuple(row[:3]):
                worksheet.update(values=[row], range_name=f"A{row_number}:E{row_number}")
                return True

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        logger.warning("Could not update the AppliTrack Google Sheet: %s", exc)
        return False