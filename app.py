import json
import os
import re
from pathlib import Path
from typing import Any, Literal, NamedTuple

import requests
import streamlit as st

API_BASE_URL = "https://applitrack-ai.onrender.com"
WEBHOOK_URL = f"{API_BASE_URL}/webhook/application"
HEALTH_URL = f"{API_BASE_URL}/health"
APPLICATIONS_URL = f"{API_BASE_URL}/applications"
SERVICE_ACCOUNT_EMAIL = (
    "applitrack-service-account@gen-lang-client-0780751036.iam.gserviceaccount.com"
)
PLATFORMS = (
    "Unstop",
    "LinkedIn",
    "Upwork",
    "Indeed",
    "Glassdoor",
    "Lever",
    "Greenhouse",
    "Workday",
    "Email Notification",
    "Custom Portal",
)
CONFIG_PATH = Path(__file__).resolve().parent / "applitrack_config.json"
API_KEY_ENV_VAR = "APPLITRACK_API_KEY"
SHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
RAW_SHEET_ID_PATTERN = re.compile(r"^[a-zA-Z0-9-_]{10,}$")
REQUEST_TIMEOUT = 30
REFRESH_SECONDS = 30
LOG_LIMIT = 10

FetchState = Literal["ok", "empty", "unauthorized", "unreachable", "error"]


class FetchResult(NamedTuple):
    state: FetchState
    rows: list[dict[str, Any]]
    message: str


def extract_sheet_id(value: str) -> str | None:
    """Return the spreadsheet ID from a raw ID or a full Google Sheets URL."""
    candidate = value.strip()
    if not candidate:
        return None

    url_match = SHEET_URL_PATTERN.search(candidate)
    if url_match:
        return url_match.group(1)

    if RAW_SHEET_ID_PATTERN.match(candidate):
        return candidate

    return None


def load_saved_sheet_id() -> str:
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    sheet_id = saved.get("sheet_id", "") if isinstance(saved, dict) else ""
    return sheet_id if isinstance(sheet_id, str) else ""


def save_sheet_id(sheet_id: str) -> None:
    """Persist the one-time sheet configuration; the API key is never written to disk."""
    try:
        CONFIG_PATH.write_text(json.dumps({"sheet_id": sheet_id}, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        st.warning(f"Could not save the configuration to {CONFIG_PATH.name}: {exc}")


def check_health() -> tuple[bool, str]:
    """Verify the deployed FastAPI service is reachable."""
    try:
        response = requests.get(HEALTH_URL, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return False, f"Service unreachable: {exc}"

    if response.status_code != 200:
        return False, f"Service returned HTTP {response.status_code}: {response.text.strip()}"

    return True, response.text.strip()


def fetch_applications(sheet_id: str, limit: int, api_key: str) -> FetchResult:
    """Read the recent application log for a sheet from the AppliTrack AI service."""
    params: dict[str, Any] = {"sheet_id": sheet_id, "limit": limit}
    if api_key:
        params["client_api_key"] = api_key

    try:
        response = requests.get(APPLICATIONS_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return FetchResult("unreachable", [], f"Could not reach the AppliTrack AI service: {exc}")

    if response.status_code == 401:
        return FetchResult(
            "unauthorized",
            [],
            "The service rejected the Client API Key. Check the key and that it matches "
            "APPLITRACK_API_KEY on Render.",
        )

    if response.status_code == 502:
        return FetchResult(
            "error",
            [],
            "The service could not read your Google Sheet. Confirm it is shared as Editor "
            f"with {SERVICE_ACCOUNT_EMAIL}.",
        )

    if response.status_code != 200:
        detail = response.text.strip() or response.reason
        return FetchResult("error", [], f"Service returned HTTP {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError:
        return FetchResult("error", [], "The service returned a response that was not valid JSON.")

    if not isinstance(payload, list):
        return FetchResult("error", [], "The service returned an unexpected response shape.")

    if not payload:
        return FetchResult("empty", [], "No applications have been logged to this sheet yet.")

    rows = [row for row in payload if isinstance(row, dict)]
    return FetchResult("ok", rows, f"{len(rows)} recent application(s) read from your sheet.")


def render_configuration_section() -> tuple[str, str]:
    st.header("1. One-Time Setup")

    with st.expander("Grant the service account access to your Google Sheet", expanded=True):
        st.markdown(
            f"""
            AppliTrack AI reads and writes your applications through a shared service
            account. Grant it **Editor** access once:

            1. Open your target Google Sheet.
            2. Click **Share**.
            3. Add `{SERVICE_ACCOUNT_EMAIL}` as a collaborator with the **Editor** role.
            4. Copy the Sheet URL or ID into the field below.
            """
        )

    sheet_input = st.text_input(
        "Target Google Sheet ID or URL",
        value=st.session_state.get("sheet_input", load_saved_sheet_id()),
        placeholder="https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit",
        help="A full Sheet URL is accepted; the ID is extracted automatically.",
    )

    api_key = st.text_input(
        "Client API Key (optional)",
        value=os.getenv(API_KEY_ENV_VAR, ""),
        type="password",
        placeholder="Only needed when APPLITRACK_API_KEY is set on Render",
        help="Never written to disk. Held in this browser session only.",
    )

    detected = extract_sheet_id(sheet_input)
    if detected and detected != sheet_input.strip():
        st.caption(f"Detected Sheet ID: `{detected}`")

    if st.button("Save configuration", type="primary"):
        if not detected:
            st.error("That does not look like a Sheet URL or ID. Check the value above.")
        else:
            save_sheet_id(detected)
            st.session_state["sheet_input"] = detected
            st.session_state["configured"] = True
            st.success(f"Saved. Sheet ID `{detected}` will be used for automated syncs.")

    return detected or load_saved_sheet_id(), api_key.strip()


def render_status_section(sheet_id: str, api_key: str) -> FetchResult | None:
    st.header("2. Agent Status")

    if not sheet_id:
        st.info("Waiting for configuration: enter your Google Sheet ID or URL above.")
        return None

    healthy, detail = check_health()
    result = fetch_applications(sheet_id, LOG_LIMIT, api_key)

    if healthy and result.state in {"ok", "empty"}:
        st.success("**Agent Status: Active & Monitoring**")
        st.caption(
            f"Webhook endpoint online ({detail}). Reading `{sheet_id}` on every "
            f"{REFRESH_SECONDS}s refresh."
        )
        return result

    st.error("**Agent Status: Offline**")
    if not healthy:
        st.error(f"The AppliTrack AI service is not responding: {detail}")

    return result


def render_integration_section() -> None:
    st.header("3. Connected Accounts & Automated Webhooks")

    st.markdown(
        "Every supported platform forwards to the same single endpoint. Configure the "
        "forwarder once and the agent logs applications and status updates with no "
        "further interaction."
    )

    st.text_input("Webhook Endpoint", value=WEBHOOK_URL)
    st.caption(
        "POST JSON with `sheet_id` and `resume_text`. Add `client_api_key` when "
        "`APPLITRACK_API_KEY` is set on the service."
    )

    st.code(
        """{
  "sheet_id": "<your-sheet-id>",
  "resume_text": "Platform: LinkedIn\\n\\nYour application was received.",
  "client_api_key": "<optional>"
}""",
        language="json",
    )

    with st.expander("1-click integration steps"):
        st.markdown(
            f"""
            **Email auto-forwarding (Zapier / Make)**
            1. Create a Zap or scenario with an email trigger matching your job
               notifications (LinkedIn, Indeed, Glassdoor, Upwork, Workday, Greenhouse).
            2. Add a **Webhook / HTTP POST** action pointing at `{WEBHOOK_URL}`.
            3. Map your sheet ID to `sheet_id` and the email body to `resume_text`.
            4. Set the prefix to `Platform:` so classification stays accurate.

            **Gmail + Apps Script**
            1. Label job notifications with a filter, for example `job-updates`.
            2. Run a time-driven Apps Script that reads the label and POSTs the message
               body to the endpoint above.

            **Background browser extension or local script**
            1. Run an extension or Playwright script on your own machine.
            2. Watch the notification pages for {", ".join(PLATFORMS[:8])}.
            3. POST the visible text to the endpoint whenever new content appears.

            Supported origins: {", ".join(PLATFORMS)}.
            """
        )


def render_activity_section(result: FetchResult | None) -> None:
    st.header("4. Agent Activity & Status Updates Log")

    if result is None:
        st.info("The log appears here once a valid Google Sheet ID is configured.")
        return

    if result.state == "ok":
        st.caption(result.message)
        st.dataframe(result.rows, width="stretch", hide_index=True)
        return

    st.warning(result.message)


@st.fragment(run_every=f"{REFRESH_SECONDS}s")
def render_live_sections(sheet_id: str, api_key: str) -> None:
    result = render_status_section(sheet_id, api_key)
    render_activity_section(result)


def main() -> None:
    st.set_page_config(page_title="AppliTrack AI Agent", page_icon="📡", layout="centered")
    st.title("Autonomous Agent Sync Dashboard")
    st.caption("AppliTrack AI watches your job platforms and syncs applications to your Sheet.")

    sheet_id, api_key = render_configuration_section()
    if sheet_id:
        render_live_sections(sheet_id, api_key)
    else:
        render_integration_section()


if __name__ == "__main__":
    main()