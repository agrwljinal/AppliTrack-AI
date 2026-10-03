import re
from typing import Any

import requests
import streamlit as st

WEBHOOK_URL = "https://applitrack-ai.onrender.com/webhook/application"
HEALTH_URL = "https://applitrack-ai.onrender.com/health"
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
CUSTOM_PORTAL = "Custom Portal"
SHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
RAW_SHEET_ID_PATTERN = re.compile(r"^[a-zA-Z0-9-_]{10,}$")
REQUEST_TIMEOUT = 30


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


def build_resume_text(platform: str, details: str) -> str:
    """Combine the platform origin and the raw application text for classification."""
    return f"Platform: {platform}\n\n{details.strip()}"


def post_application(sheet_id: str, resume_text: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Send one application to the AppliTrack AI webhook."""
    try:
        response = requests.post(
            WEBHOOK_URL,
            json={"sheet_id": sheet_id, "resume_text": resume_text},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach the AppliTrack AI service: {exc}", None

    if response.status_code != 200:
        detail = response.text.strip()
        if not detail:
            detail = response.reason
        return False, f"Service returned HTTP {response.status_code}: {detail}", None

    try:
        payload = response.json()
    except ValueError:
        payload = None

    application = payload.get("application", {}) if isinstance(payload, dict) else {}
    summary = (
        f"{application.get('company_name', 'Unknown company')} - "
        f"{application.get('role', 'Unknown role')} "
        f"[{application.get('status', 'Unknown')}]"
    )
    return True, f"Application logged to your sheet: {summary}", payload


def check_health() -> tuple[bool, str]:
    """Verify the deployed FastAPI service is reachable."""
    try:
        response = requests.get(HEALTH_URL, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return False, f"Service unreachable: {exc}"

    if response.status_code != 200:
        return False, f"Service returned HTTP {response.status_code}: {response.text.strip()}"

    return True, response.text.strip()


def render_setup_section() -> tuple[str | None, str]:
    st.header("1. Google Sheet Access")
    st.markdown(
        f"""
        AppliTrack AI writes every application to your own Google Sheet through a
        shared service account. Grant it **Editor** access before you sync:

        1. Open your target Google Sheet.
        2. Click **Share**.
        3. Add `{SERVICE_ACCOUNT_EMAIL}` as a collaborator with the **Editor** role.
        4. Copy the Sheet URL or ID into the field below.
        """
    )

    raw_sheet = st.text_input(
        "Target Google Sheet ID or URL",
        placeholder="https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit",
        help="A full Sheet URL is accepted; the ID is extracted automatically.",
    )

    sheet_id = extract_sheet_id(raw_sheet)
    if sheet_id:
        st.success(f"Detected Sheet ID: `{sheet_id}`")
    elif raw_sheet.strip():
        st.warning("That does not look like a Sheet URL or ID. Check the value above.")

    return sheet_id, raw_sheet


def render_application_section(sheet_id: str | None) -> None:
    st.header("2. Application Details")

    platform = st.selectbox("Platform origin", PLATFORMS)
    custom_platform = ""
    if platform == CUSTOM_PORTAL:
        custom_platform = st.text_input(
            "Custom portal name",
            placeholder="Company careers page, referral form, ...",
        ).strip()
        if not custom_platform:
            st.info("Enter the custom portal name to enable syncing.")

    details = st.text_area(
        "Application status, confirmation text, HTML snippet, or email body",
        height=220,
        placeholder=(
            "Company: Acme Corp\n"
            "Role: Software Engineer\n"
            "Your application for the Software Engineer role has been received."
        ),
        help="Paste the raw text from any supported platform. AppliTrack AI classifies it automatically.",
    )

    submitted = st.button("Sync & Log Application", type="primary")

    if not submitted:
        return

    if not sheet_id:
        st.error("Provide a valid Google Sheet ID or URL first.")
        return

    if platform == CUSTOM_PORTAL and not custom_platform:
        st.error("Enter the custom portal name first.")
        return

    if not details.strip():
        st.error("Paste the application details first.")
        return

    with st.spinner("Syncing with AppliTrack AI..."):
        success, message, payload = post_application(
            sheet_id,
            build_resume_text(custom_platform or platform, details),
        )

    if success:
        st.success(message)
        if payload is not None:
            with st.expander("Backend response"):
                st.json(payload)
    else:
        st.error(message)


def render_sync_settings_section() -> None:
    st.header("3. Multi-Platform Automated Sync Settings")

    st.markdown(
        f"""
        Forward notifications from any supported portal (Unstop, LinkedIn, Upwork,
        Indeed, Glassdoor, Lever, Greenhouse, Workday, email, or a custom careers
        page) to the webhook below to log applications without using this dashboard.
        """
    )

    st.text_input("Webhook URL", value=WEBHOOK_URL)
    st.caption("POST JSON: `sheet_id` and `resume_text`.")

    if st.button("Test service connection"):
        with st.spinner("Contacting the AppliTrack AI service..."):
            healthy, detail = check_health()
        if healthy:
            st.success(f"Service online: {detail}")
        else:
            st.error(detail)

    st.subheader("Session and cookie storage")
    st.warning(
        "Portal sessions and cookies grant direct access to your job accounts. They are "
        "never sent to AppliTrack AI, and this dashboard only keeps them in this browser "
        "session - reloading the page clears them. Prefer a local extension or script over "
        "pasting cookies into a hosted web form."
    )

    session_platform = st.selectbox("Session belongs to", PLATFORMS, key="session_platform")
    st.text_input(
        "Cookie value or session token",
        type="password",
        placeholder="Leave blank unless an automation requires it",
        key="session_cookie",
    )
    st.text_input(
        "Local cookie jar path",
        placeholder=r"C:\Users\you\cookies\linkedin.json",
        help="Reference to a cookie file on the machine running the forwarder.",
        key="cookie_path",
    )
    st.caption(
        f"Session retained for {session_platform} in this session only. "
        "Nothing is written to disk or sent to the backend."
    )

    with st.expander("Automated forwarding options"):
        st.markdown(
            f"""
            **Browser extension** - Watch portal notification pages and POST the visible
            text to `{WEBHOOK_URL}` with your `sheet_id`.

            **Email parser** - Forward portal emails to an address handled by a parser
            (for example a Gmail filter plus Apps Script or a mail-to-webhook service)
            that sends the message body as `resume_text`.

            **API webhooks** - Poll the platform notification endpoints from your own
            machine or server and forward each payload to the webhook URL.

            **Local script** - Run a Playwright or Selenium notifier that reads the
            notification DOM and posts the extracted text.

            Example payload:

            ```json
            {{
              "sheet_id": "<your-sheet-id>",
              "resume_text": "Platform: LinkedIn\\n\\nYour application was received."
            }}
            ```
            """
        )


def main() -> None:
    st.set_page_config(page_title="AppliTrack AI Dashboard", page_icon="📊", layout="centered")
    st.title("AppliTrack AI Dashboard")
    st.caption("Log job applications from any platform into your own Google Sheet.")

    sheet_id, _ = render_setup_section()
    render_application_section(sheet_id)
    render_sync_settings_section()


if __name__ == "__main__":
    main()