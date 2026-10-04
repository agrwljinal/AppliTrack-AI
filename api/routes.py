import hmac
import json
import logging
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Response
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.responses import JSONResponse, RedirectResponse

from services.ai_classifier import classify_application
from services.gmail_service import (
    CLIENT_ID_ENV_VAR,
    CLIENT_SECRET_ENV_VAR,
    DEFAULT_REDIRECT_URI,
    DEFAULT_SHEET_ENV_VAR,
    GMAIL_SCOPES,
    REDIRECT_URI_ENV_VAR,
    OAuthConfigurationError,
    build_oauth_state,
    connection_status,
    exchange_code_for_tokens,
    fetch_and_sync_user_emails,
    resolve_sheet_id,
    run_ingestion_cycle,
)
from services.sheet_service import read_recent_applications, update_or_append_application

router = APIRouter()
logger = logging.getLogger(__name__)
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


def streamlit_ui_url(outcome: str) -> str:
    base_url = (os.getenv(STREAMLIT_UI_ENV_VAR) or "").strip() or DEFAULT_STREAMLIT_UI_URL
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'auth': outcome})}"


def dashboard_callback_url(code: str, state: str) -> str:
    """Hand the authorization code to the dashboard so it can finish the handshake.

    Used only when this process cannot exchange the code itself. `state` must be
    carried over: it holds the signed sheet binding the dashboard needs to know
    which spreadsheet to attach the account to.
    """
    base_url = (os.getenv(STREAMLIT_UI_ENV_VAR) or "").strip() or DEFAULT_STREAMLIT_UI_URL
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'code': code, 'state': state})}"


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


@router.get(
    "/auth/google/login",
    responses={
        307: {"description": "Redirect to the Google consent screen."},
        400: {"description": "No Google Sheet ID is available for this request."},
        503: {"description": "Google OAuth is not configured, or the flow could not start."},
    },
)
def google_login(sheet_id: str | None = None) -> Response:
    """Start the 1-click Gmail connection by redirecting to Google's consent screen."""
    try:
        client_id = (os.getenv(CLIENT_ID_ENV_VAR) or "").strip()
        client_secret = (os.getenv(CLIENT_SECRET_ENV_VAR) or "").strip()
        redirect_uri = (os.getenv(REDIRECT_URI_ENV_VAR) or "").strip() or DEFAULT_REDIRECT_URI

        missing = [
            name
            for name, value in ((CLIENT_ID_ENV_VAR, client_id), (CLIENT_SECRET_ENV_VAR, client_secret))
            if not value
        ]
        if missing:
            raise OAuthConfigurationError(
                f"Google OAuth is not configured. Set {', '.join(missing)} on the service."
            )

        target_sheet = resolve_sheet_id(sheet_id)
        if not target_sheet:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"A sheet_id is required, or set {DEFAULT_SHEET_ENV_VAR} on the service."
                    )
                },
            )

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
            },
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri,
        )
        authorization_url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=build_oauth_state(target_sheet),
        )
    except Exception as exc:
        logger.warning("Could not start the Google OAuth flow: %s", exc)
        return JSONResponse(status_code=503, content={"error": str(exc)})

    return RedirectResponse(url=authorization_url, status_code=307)


@router.get("/auth/google/callback")
def google_callback(
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
) -> RedirectResponse:
    """Finish the OAuth handshake and send the user back to the dashboard."""
    try:
        exchange_code_for_tokens(code, state)
    except ValueError as exc:
        # Invalid, expired, or already-redeemed code, or a state that does not
        # verify. There is nothing to hand off, so report it instead.
        return RedirectResponse(streamlit_ui_url(f"error:{exc}"), status_code=307)
    except Exception:
        # Missing or unusable credentials here; the dashboard may still succeed.
        logger.warning(
            "Could not exchange the OAuth code in the API process; "
            "handing it to the dashboard instead.",
            exc_info=True,
        )
        return RedirectResponse(dashboard_callback_url(code, state), status_code=307)

    return RedirectResponse(streamlit_ui_url("success"), status_code=307)


@router.get("/auth/google/status")
def google_status(
    sheet_id: str = Query(min_length=1),
    client_api_key: str | None = None,
) -> dict[str, Any]:
    """Report whether a Gmail account is connected, never raising on store problems."""
    require_valid_api_key(client_api_key)
    return connection_status(sheet_id.strip())


@router.post("/sync/gmail")
@router.post("/auth/gmail/sync", include_in_schema=False)
def trigger_gmail_sync(
    sheet_id: str | None = None,
    client_api_key: str | None = None,
) -> dict[str, Any]:
    """Run Gmail ingestion on demand, for one sheet or every connected account."""
    require_valid_api_key(client_api_key)

    if sheet_id:
        target_sheet = resolve_sheet_id(sheet_id)
        if not target_sheet:
            raise HTTPException(status_code=400, detail="A sheet_id is required.")
        return fetch_and_sync_user_emails(target_sheet)

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