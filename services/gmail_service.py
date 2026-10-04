"""Google OAuth 2.0 connection and Gmail ingestion engine for AppliTrack AI."""

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from services.ai_classifier import classify_application
from services.sheet_service import update_or_append_application

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_STORE_PATH = PROJECT_ROOT / "gmail_tokens.json"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_ID_ENV_VAR = "GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "GOOGLE_CLIENT_SECRET"
REDIRECT_URI_ENV_VAR = "GOOGLE_REDIRECT_URI"
TOKEN_KEY_ENV_VAR = "APPLITRACK_TOKEN_KEY"
POLL_SECONDS_ENV_VAR = "APPLITRACK_GMAIL_POLL_SECONDS"
DEFAULT_SHEET_ENV_VAR = "GOOGLE_SHEET_ID"

DEFAULT_REDIRECT_URI = "https://applitrack-ai.onrender.com"
DEFAULT_POLL_SECONDS = 300
STATE_MAX_AGE_SECONDS = 600
DEFAULT_GMAIL_QUERY = (
    # Gmail has no parentheses grouping and no OR keyword; {a b} is its OR operator.
    # Each alternative is a separate subject: term so the OR applies to whole phrases.
    # Deliberately no is:unread: reading a confirmation in Gmail must not stop it
    # being logged. Duplicates are already filtered by each account's processed_ids.
    '{subject:submitted subject:received subject:"thank you for applying" '
    'subject:"application confirmation"} -unsubscribe -"recommended opportunities" '
    '-"job alert"'
)
MAX_MESSAGES_PER_RUN = 25
MAX_PROCESSED_IDS = 500

PLATFORM_HINTS = (
    ("linkedin", "LinkedIn"),
    ("unstop", "Unstop"),
    ("indeed", "Indeed"),
    ("glassdoor", "Glassdoor"),
    ("upwork", "Upwork"),
    ("workday", "Workday"),
    ("greenhouse", "Greenhouse"),
    ("lever", "Lever"),
)

logger = logging.getLogger(__name__)

_store_lock = threading.Lock()
_poller_thread: threading.Thread | None = None


class OAuthConfigurationError(RuntimeError):
    """Raised when the Google OAuth environment variables are missing or invalid."""


# --------------------------------------------------------------------------- config


def redirect_uri() -> str:
    return (os.getenv(REDIRECT_URI_ENV_VAR) or "").strip() or DEFAULT_REDIRECT_URI


def oauth_configured() -> bool:
    return bool(
        (os.getenv(CLIENT_ID_ENV_VAR) or "").strip()
        and (os.getenv(CLIENT_SECRET_ENV_VAR) or "").strip()
    )


def poll_seconds() -> int:
    raw_value = (os.getenv(POLL_SECONDS_ENV_VAR) or "").strip()
    if not raw_value.isdigit():
        return DEFAULT_POLL_SECONDS
    return max(60, int(raw_value))


def resolve_sheet_id(sheet_id: str | None) -> str | None:
    """Use the requested sheet, else fall back to the service-wide default sheet."""
    candidate = (sheet_id or "").strip()
    if candidate:
        return candidate

    default_sheet = (os.getenv(DEFAULT_SHEET_ENV_VAR) or "").strip()
    return default_sheet or None


def _require_oauth_config() -> tuple[str, str, str]:
    client_id = (os.getenv(CLIENT_ID_ENV_VAR) or "").strip()
    client_secret = (os.getenv(CLIENT_SECRET_ENV_VAR) or "").strip()
    if not client_id or not client_secret:
        missing = [
            name
            for name, value in ((CLIENT_ID_ENV_VAR, client_id), (CLIENT_SECRET_ENV_VAR, client_secret))
            if not value
        ]
        raise OAuthConfigurationError(
            f"Google OAuth is not configured. Set {', '.join(missing)} on the service."
        )
    return client_id, client_secret, redirect_uri()


# --------------------------------------------------------------------------- state signing


def _state_secret() -> bytes:
    """Sign OAuth state with the client secret; no extra secret to provision."""
    return (os.getenv(CLIENT_SECRET_ENV_VAR) or "applitrack-dev-state").encode("utf-8")


def build_oauth_state(sheet_id: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sheet_id": sheet_id,
                "nonce": secrets.token_urlsafe(16),
                "issued_at": int(time.time()),
            }
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(_state_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_oauth_state(state: str) -> str:
    """Return the sheet ID encoded in a signed state value, or raise on tampering."""
    payload, _, signature = state.partition(".")
    if not payload or not signature:
        raise ValueError("Malformed OAuth state")

    expected = hmac.new(_state_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("OAuth state signature does not match")

    padded = payload + "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    issued_at = int(data.get("issued_at", 0))
    if time.time() - issued_at > STATE_MAX_AGE_SECONDS:
        raise ValueError("OAuth state expired; restart the connection")

    sheet_id = data.get("sheet_id")
    if not isinstance(sheet_id, str) or not sheet_id.strip():
        raise ValueError("OAuth state is missing the sheet ID")
    return sheet_id.strip()


# --------------------------------------------------------------------------- credential store


def _fernet() -> Fernet | None:
    key = (os.getenv(TOKEN_KEY_ENV_VAR) or "").strip()
    if not key:
        logger.warning(
            "%s is not set; Gmail tokens are stored without encryption at rest.",
            TOKEN_KEY_ENV_VAR,
        )
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        logger.warning("%s is not a valid Fernet key: %s", TOKEN_KEY_ENV_VAR, exc)
        return None


def _encrypt(raw: bytes) -> bytes:
    cipher = _fernet()
    return cipher.encrypt(raw) if cipher else raw


def _decrypt(raw: bytes) -> bytes:
    cipher = _fernet()
    if cipher is None:
        return raw
    try:
        return cipher.decrypt(raw)
    except InvalidToken:
        raise OAuthConfigurationError(
            "Stored Gmail tokens cannot be decrypted. Check APPLITRACK_TOKEN_KEY."
        ) from None


def read_store() -> dict[str, Any]:
    if not TOKEN_STORE_PATH.is_file():
        return {}

    try:
        blob = TOKEN_STORE_PATH.read_bytes()
        store = json.loads(_decrypt(blob).decode("utf-8"))
    except OAuthConfigurationError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("Could not read the Gmail token store: %s", exc)
        return {}

    accounts = store.get("accounts") if isinstance(store, dict) else None
    return accounts if isinstance(accounts, dict) else {}


def write_store(accounts: dict[str, Any]) -> None:
    payload = json.dumps({"accounts": accounts}, indent=2).encode("utf-8")
    with _store_lock:
        TOKEN_STORE_PATH.write_bytes(_encrypt(payload))
        try:
            TOKEN_STORE_PATH.chmod(0o600)
        except OSError:
            logger.debug("Could not restrict permissions on the Gmail token store.")


def save_connected_account(email: str, sheet_id: str, token: dict[str, Any]) -> None:
    accounts = read_store()
    accounts[email.casefold()] = {
        "email": email,
        "sheet_id": sheet_id,
        "token": token,
        "connected_at": datetime.now(UTC).isoformat(),
        "processed_ids": accounts.get(email.casefold(), {}).get("processed_ids", []),
    }
    write_store(accounts)
    logger.info("Stored Gmail credentials for %s (sheet %s).", email, sheet_id)


def disconnect_account(email: str) -> bool:
    accounts = read_store()
    if accounts.pop(email.casefold(), None) is None:
        return False
    write_store(accounts)
    return True


def connected_emails() -> list[str]:
    return sorted(read_store())


def account_for_sheet(sheet_id: str) -> dict[str, Any] | None:
    target = sheet_id.strip().casefold()
    for account in read_store().values():
        if str(account.get("sheet_id", "")).strip().casefold() == target:
            return account
    return None


def connection_status(sheet_id: str) -> dict[str, Any]:
    """Report Gmail configuration and connection state for a sheet. Never raises.

    Shared by the FastAPI route and the Streamlit dashboard so both agree.
    """
    configured = False
    account: dict[str, Any] | None = None
    emails: list[str] = []
    error: str | None = None

    try:
        configured = oauth_configured()
        account = account_for_sheet(sheet_id)
        emails = connected_emails()
    except Exception as exc:
        logger.warning("Could not read the Gmail connection status: %s", exc)
        error = str(exc)

    return {
        "configured": configured,
        "oauth_configured": configured,
        "connected": account is not None,
        "email": account.get("email") if account else None,
        "sheet_id": sheet_id.strip(),
        "connected_emails": emails,
        "error": error,
    }


# --------------------------------------------------------------------------- oauth flow


def build_oauth_flow(redirect_uri: str | None = None) -> Flow:
    """Build the OAuth flow used for both the consent URL and the token exchange.

    PKCE is disabled on purpose. `google_auth_oauthlib` 1.x turns it on by default
    (`autogenerate_code_verifier=True`), which makes `authorization_url()` send a
    `code_challenge` derived from a random verifier. The dashboard builds the
    consent URL in one request and redeems the code in another, so that verifier
    cannot survive the round trip. Leaving PKCE on makes Google reject the
    exchange with `invalid_grant` because the verifier no longer matches.
    """
    client_id, client_secret, uri = _require_oauth_config()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri or uri],
            }
        },
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri or uri,
        autogenerate_code_verifier=False,
    )


def authorization_url(sheet_id: str) -> str:
    return build_oauth_flow().authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=build_oauth_state(sheet_id),
    )[0]


def exchange_code_for_tokens(code: str, state: str) -> str:
    sheet_id = read_oauth_state(state)
    flow = build_oauth_flow()
    flow.fetch_token(code=code, state=state)

    credentials = flow.credentials
    if not credentials.refresh_token:
        raise OAuthConfigurationError(
            "Google did not return a refresh token. Revoke access and reconnect."
        )

    email = _fetch_gmail_address(credentials)
    save_connected_account(
        email,
        sheet_id,
        {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": list(credentials.scopes or GMAIL_SCOPES),
        },
    )
    return email


def _fetch_gmail_address(credentials: Credentials) -> str:
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    address = profile.get("emailAddress", "")
    if not address:
        raise OAuthConfigurationError("Google did not return a Gmail address.")
    return address


# --------------------------------------------------------------------------- gmail reading


def _credentials_from_account(account: dict[str, Any]) -> Credentials:
    credentials = Credentials.from_authorized_user_info(account.get("token") or {})
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return credentials


def gmail_service(credentials: Credentials) -> Any:
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def _strip_html(raw_html: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw_html)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    return re.sub(r"[ \t]+", " ", html.unescape(without_tags)).strip()


def _walk_parts(parts: list[dict[str, Any]], collected: dict[str, str]) -> None:
    for part in parts:
        mime_type = part.get("mimeType", "")
        body = part.get("body") or {}
        if mime_type.startswith("multipart/"):
            _walk_parts(part.get("parts") or [], collected)
        elif mime_type.startswith("text/") and body.get("data"):
            kind = "plain" if mime_type == "text/plain" else "html"
            if kind not in collected:
                collected[kind] = _decode_body(body["data"])


def extract_message_text(payload: dict[str, Any]) -> str:
    collected: dict[str, str] = {}
    _walk_parts(payload.get("payload", {}).get("parts") or [], collected)

    body = payload.get("payload", {}).get("body") or {}
    if body.get("data") and not collected:
        decoded = _decode_body(body["data"])
        if payload.get("payload", {}).get("mimeType") == "text/html":
            collected["html"] = decoded
        else:
            collected["plain"] = decoded

    if collected.get("plain", "").strip():
        return collected["plain"].strip()
    if collected.get("html", "").strip():
        return _strip_html(collected["html"])
    return ""


def infer_platform(subject: str, sender: str, body: str) -> str:
    haystack = f"{subject} {sender} {body[:600]}".casefold()
    for hint, platform in PLATFORM_HINTS:
        if hint in haystack:
            return platform
    return "Email Notification"


def fetch_job_notifications(credentials: Credentials) -> list[dict[str, str]]:
    """Return job notifications matching the intent query, with plain-text bodies."""
    query = (os.getenv("APPLITRACK_GMAIL_QUERY") or "").strip() or DEFAULT_GMAIL_QUERY
    service = gmail_service(credentials)

    listing = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=MAX_MESSAGES_PER_RUN)
        .execute()
    )

    messages: list[dict[str, str]] = []
    for reference in listing.get("messages", []) or []:
        message_id = reference.get("id")
        if not message_id:
            continue
        try:
            payload = (
                service.users().messages().get(userId="me", id=message_id, format="full").execute()
            )
        except HttpError as exc:
            logger.warning("Could not read Gmail message %s: %s", message_id, exc)
            continue

        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in payload.get("payload", {}).get("headers", [])
        }
        text = extract_message_text(payload)
        if not text:
            continue

        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        messages.append(
            {
                "id": message_id,
                "subject": subject,
                "sender": sender,
                "body": text,
                "platform": infer_platform(subject, sender, text),
            }
        )

    return messages


# --------------------------------------------------------------------------- ingestion


def sync_account(account: dict[str, Any]) -> dict[str, int]:
    """Ingest new Gmail notifications for one connected account."""
    email = str(account.get("email", ""))
    sheet_id = str(account.get("sheet_id", ""))
    processed_ids = list(account.get("processed_ids") or [])

    try:
        credentials = _credentials_from_account(account)
        messages = fetch_job_notifications(credentials)
    except Exception as exc:
        logger.warning("Gmail sync failed for %s: %s", email, exc)
        return {"fetched": 0, "logged": 0, "failed": 0}

    new_messages = [message for message in messages if message["id"] not in set(processed_ids)]
    logged = 0
    failed = 0
    for message in new_messages:
        try:
            application = classify_application(
                message["body"],
                message["platform"],
                subject=message["subject"],
                sender=message["sender"],
            )
            if update_or_append_application(application, sheet_id):
                logged += 1
            else:
                failed += 1
        except Exception:
            logger.exception("Could not process a Gmail notification for %s.", email)
            failed += 1
        processed_ids.append(message["id"])

    account["processed_ids"] = processed_ids[-MAX_PROCESSED_IDS:]
    if new_messages:
        write_store(read_store() | {email.casefold(): account})

    logger.info("Gmail sync for %s: %s fetched, %s logged.", email, len(new_messages), logged)
    return {"fetched": len(new_messages), "logged": logged, "failed": failed}


def fetch_and_sync_user_emails(sheet_id: str) -> dict[str, int]:
    """Sync every Gmail account bound to a sheet, appending parsed rows to it."""
    target = sheet_id.strip().casefold()
    totals = {"accounts": 0, "fetched": 0, "logged": 0, "failed": 0}

    for account in read_store().values():
        if str(account.get("sheet_id", "")).strip().casefold() != target:
            continue
        totals["accounts"] += 1
        result = sync_account(account)
        for key in ("fetched", "logged", "failed"):
            totals[key] += result[key]

    if totals["accounts"] == 0:
        logger.warning("No Gmail account is connected for sheet %s.", sheet_id)

    return totals


def run_ingestion_cycle() -> dict[str, int]:
    totals = {"accounts": 0, "fetched": 0, "logged": 0, "failed": 0}
    for account in read_store().values():
        totals["accounts"] += 1
        result = sync_account(account)
        for key in ("fetched", "logged", "failed"):
            totals[key] += result[key]
    return totals


def _poll_forever() -> None:
    while True:
        try:
            run_ingestion_cycle()
        except Exception:
            logger.exception("The Gmail ingestion cycle failed.")
        time.sleep(poll_seconds())


def start_background_poller() -> bool:
    """Start the periodic Gmail poller once per process."""
    global _poller_thread

    with _store_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return False
        _poller_thread = threading.Thread(
            target=_poll_forever, name="applitrack-gmail-poller", daemon=True
        )
        _poller_thread.start()
        return True