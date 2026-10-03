import hmac
import json
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from services.ai_classifier import classify_application
from services.gmail_service import (
    OAuthConfigurationError,
    account_for_sheet,
    authorization_url,
    connected_emails,
    exchange_code_for_tokens,
    oauth_configured,
    run_ingestion_cycle,
)
from services.sheet_service import read_recent_applications, update_or_append_application

router = APIRouter()
API_KEY_ENV_VAR = "APPLITRACK_API_KEY"
STREAMLIT_UI_ENV_VAR = "STREAMLIT_UI_URL"
DEFAULT_STREAMLIT_UI_URL = "http://localhost:8501"
MAX_APPLICATION_LIMIT = 100


def verify_client_api_key(provided_key: str | None) -> bool:
    """Accept any caller when APPLITRACK_API_KEY is unset, otherwise require a match."""
    expected_key = (os.getenv(API_KEY_ENV_VAR) or "").strip()
    if not expected_key:
        return True

    candidate = (provided_key or "").strip()
    return bool(candidate) and hmac.compare_digest(candidate, expected_key)


def require_valid_api_key(provided_key: str | None) -> None:
    if not verify_client_api_key(provided_key):
        raise HTTPException(status_code=401, detail="Invalid or missing client_api_key")


def streamlit_ui_url(status: str) -> str:
    base_url = (os.getenv(STREAMLIT_UI_ENV_VAR) or "").strip() or DEFAULT_STREAMLIT_UI_URL
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'gmail': status})}"


class WebhookPayload(BaseModel):
    sheet_id: str = Field(min_length=1)
    applicant_data: dict[str, Any] | str | None = None
    resume_text: str | None = None
    client_api_key: str | None = None

    @field_validator("sheet_id")
    @classmethod
    def normalize_sheet_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sheet_id must not be empty")
        return value

    @model_validator(mode="after")
    def require_application_content(self) -> "WebhookPayload":
        applicant_data_present = self.applicant_data is not None
        if isinstance(self.applicant_data, str):
            applicant_data_present = bool(self.applicant_data.strip())
        if not (self.resume_text and self.resume_text.strip()) and not applicant_data_present:
            raise ValueError("Provide applicant_data or non-empty resume_text")
        return self


@router.post("/webhook/application")
def receive_application(payload: WebhookPayload) -> dict[str, Any]:
    require_valid_api_key(payload.client_api_key)

    if payload.resume_text and payload.resume_text.strip():
        raw_text = payload.resume_text
    elif isinstance(payload.applicant_data, str):
        raw_text = payload.applicant_data
    else:
        raw_text = json.dumps(payload.applicant_data, ensure_ascii=True)

    platform_used = "Unknown"
    if isinstance(payload.applicant_data, dict):
        value = payload.applicant_data.get("platform_used")
        if isinstance(value, str) and value.strip():
            platform_used = value.strip()

    application = classify_application(raw_text, platform_used)
    if not update_or_append_application(application, payload.sheet_id):
        raise HTTPException(status_code=502, detail="Could not write application to the client sheet")

    return {
        "status": "processed",
        "application": application.model_dump(mode="json"),
        "sheet_updated": True,
    }


@router.get("/auth/google/login")
def google_login(sheet_id: str = Query(min_length=1)) -> RedirectResponse:
    """Start the 1-click Gmail connection by redirecting to Google's consent screen."""
    if not oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured on this service.",
        )

    try:
        return RedirectResponse(authorization_url(sheet_id.strip()), status_code=307)
    except OAuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/auth/google/callback")
def google_callback(
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
) -> RedirectResponse:
    """Finish the OAuth handshake and send the user back to the dashboard."""
    try:
        exchange_code_for_tokens(code, state)
    except ValueError as exc:
        return RedirectResponse(streamlit_ui_url(f"error:{exc}"), status_code=307)
    except OAuthConfigurationError as exc:
        return RedirectResponse(streamlit_ui_url(f"error:{exc}"), status_code=307)

    return RedirectResponse(streamlit_ui_url("connected"), status_code=307)


@router.get("/auth/google/status")
def google_status(
    sheet_id: str = Query(min_length=1),
    client_api_key: str | None = None,
) -> dict[str, Any]:
    """Report whether a Gmail account is connected for the given sheet."""
    require_valid_api_key(client_api_key)

    account = account_for_sheet(sheet_id.strip())
    return {
        "oauth_configured": oauth_configured(),
        "connected": account is not None,
        "email": account.get("email") if account else None,
        "sheet_id": sheet_id.strip(),
        "connected_emails": connected_emails(),
    }


@router.post("/auth/gmail/sync")
def trigger_gmail_sync(client_api_key: str | None = None) -> dict[str, Any]:
    """Run one ingestion cycle on demand."""
    require_valid_api_key(client_api_key)
    return run_ingestion_cycle()


@router.get("/applications")
def list_applications(
    sheet_id: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=MAX_APPLICATION_LIMIT),
    client_api_key: str | None = None,
) -> list[dict[str, str]]:
    """Return the most recently logged applications for a client sheet."""
    require_valid_api_key(client_api_key)

    applications = read_recent_applications(sheet_id.strip(), limit)
    if applications is None:
        raise HTTPException(
            status_code=502,
            detail="Could not read the client sheet. Confirm the service account has Editor access.",
        )

    return applications


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "AppliTrack AI"}