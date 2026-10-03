import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
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

BRAND_TITLE = "AppliTrack AI"
BRAND_TAGLINE = "Autonomous Job Application Tracking & Sheet Sync Engine"

LOGO_SVG = """
<svg width="58" height="58" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"
     role="img" aria-label="AppliTrack AI logo">
  <defs>
    <linearGradient id="apLogoGradient" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
      <stop stop-color="#4F46E5"/>
      <stop offset="1" stop-color="#06B6D4"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="17" fill="url(#apLogoGradient)"/>
  <path d="M32 16v-4" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round"/>
  <circle cx="32" cy="10" r="3.2" fill="#FFFFFF"/>
  <rect x="15" y="22" width="34" height="25" rx="9" fill="#FFFFFF"/>
  <circle cx="25" cy="33" r="3.4" fill="#4F46E5"/>
  <circle cx="39" cy="33" r="3.4" fill="#4F46E5"/>
  <rect x="24" y="40" width="16" height="3.2" rx="1.6" fill="#4F46E5"/>
  <circle cx="52" cy="52" r="6" fill="#22C55E" stroke="#FFFFFF" stroke-width="2.5"/>
</svg>
"""

APP_STYLES = """
<style>
  .stApp {
    background-image: linear-gradient(180deg, #f8fafc 0%, #eef2f9 100%);
    background-attachment: fixed;
  }
  .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 900px; }

  .ap-hero {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 6px 0 4px 0;
  }
  .ap-hero-logo { flex: 0 0 auto; line-height: 0; filter: drop-shadow(0 6px 14px rgba(79, 70, 229, .28)); }
  .ap-hero-title {
    font-size: 2.55rem;
    font-weight: 800;
    letter-spacing: -0.025em;
    color: #0f172a;
    margin: 0;
    line-height: 1.1;
  }
  .ap-hero-tagline {
    font-size: 1.02rem;
    font-weight: 500;
    color: #64748b;
    margin: 4px 0 0 0;
  }
  .ap-hero-badges { margin: 14px 0 22px 0; }

  [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px;
    background-color: #ffffff;
    border: 1px solid #e6ebf3;
    box-shadow: 0 6px 20px rgba(15, 23, 42, 0.06);
    padding: 1.15rem 1.4rem 1.4rem 1.4rem;
    margin-bottom: 1.15rem;
  }
  .ap-card-title {
    display: flex;
    align-items: center;
    gap: 11px;
    font-size: 1.12rem;
    font-weight: 700;
    color: #0f172a;
    margin: 0 0 0.15rem 0;
    letter-spacing: -0.01em;
  }
  .ap-step {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 26px;
    height: 26px;
    padding: 0 7px;
    border-radius: 9px;
    background-image: linear-gradient(135deg, #4F46E5, #06B6D4);
    color: #ffffff;
    font-size: 0.78rem;
    font-weight: 700;
    box-shadow: 0 3px 8px rgba(79, 70, 229, 0.28);
  }
  .ap-card-sub {
    font-size: 0.9rem;
    color: #64748b;
    margin: 2px 0 0.9rem 0;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 11px;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 600;
    line-height: 1.55;
    margin: 0 6px 6px 0;
    white-space: nowrap;
  }
  .pill-green { background-color: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
  .pill-red { background-color: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }
  .pill-blue { background-color: #e0edff; color: #1d4ed8; border: 1px solid #c7dbff; }
  .pill-slate { background-color: #eef2f7; color: #475569; border: 1px solid #e2e8f0; }
  .pill-dot { width: 7px; height: 7px; border-radius: 50%; background-color: currentColor; }

  .ap-section-label {
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: #94a3b8;
    margin: 1.1rem 0 0.35rem 0;
  }

  [data-testid="stExpander"] {
    border: 1px solid #e6ebf3;
    border-radius: 12px;
    background-color: #fbfcfe;
  }
  [data-testid="stExpander"] summary p { font-weight: 600; color: #1e293b; }

  footer, [data-testid="stStatusWidget"] { visibility: hidden; height: 0; }
  .stButton > button[kind="primary"] {
    border-radius: 10px;
    font-weight: 600;
    box-shadow: 0 4px 12px rgba(79, 70, 229, 0.22);
  }
  [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea { border-radius: 10px; }
</style>
"""


class FetchResult(NamedTuple):
    state: FetchState
    rows: list[dict[str, Any]]
    message: str


def inject_styles() -> None:
    st.markdown(APP_STYLES, unsafe_allow_html=True)


@contextmanager
def card(step: str, title: str, subtitle: str = "") -> Iterator[None]:
    """Render one dashboard section inside a rounded, shadowed card."""
    with st.container(border=True):
        st.markdown(
            f'<div class="ap-card-title"><span class="ap-step">{step}</span>{title}</div>',
            unsafe_allow_html=True,
        )
        if subtitle:
            st.markdown(f'<div class="ap-card-sub">{subtitle}</div>', unsafe_allow_html=True)
        yield


def pill(label: str, tone: str = "slate", dotted: bool = False) -> str:
    dot = '<span class="pill-dot"></span>' if dotted else ""
    return f'<span class="pill pill-{tone}">{dot}{label}</span>'


def render_brand_header() -> None:
    st.markdown(
        f"""
        <div class="ap-hero">
          <div class="ap-hero-logo">{LOGO_SVG}</div>
          <div>
            <h1 class="ap-hero-title">{BRAND_TITLE}</h1>
            <p class="ap-hero-tagline">{BRAND_TAGLINE}</p>
          </div>
        </div>
        <div class="ap-hero-badges">
          {pill("Privacy-First", "slate")}
          {pill("Google Sheets Native", "blue")}
          {pill(f"Refreshes every {REFRESH_SECONDS}s", "green", dotted=True)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_platform_pills() -> None:
    st.markdown('<div class="ap-section-label">Supported Origins</div>', unsafe_allow_html=True)
    st.markdown(
        "".join(pill(platform, "blue") for platform in PLATFORMS),
        unsafe_allow_html=True,
    )


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
    with card(
        "1",
        "One-Time Setup",
        "Share your sheet once. The agent handles everything after this.",
    ):
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
    with card("2", "Agent Status", "Live connectivity and sync state."):
        if not sheet_id:
            st.info("Waiting for configuration: enter your Google Sheet ID or URL above.")
            return None

        healthy, detail = check_health()
        result = fetch_applications(sheet_id, LOG_LIMIT, api_key)
        ready = healthy and result.state in {"ok", "empty"}
        sheet_reachable = result.state in {"ok", "empty"}

        st.markdown(
            "".join(
                [
                    pill("Agent Ready", "green", dotted=True)
                    if ready
                    else pill("Agent Offline", "red", dotted=True),
                    pill("Service Online", "green") if healthy else pill("Service Offline", "red"),
                    pill("Sheet Connected", "green")
                    if sheet_reachable
                    else pill("Sheet Unreadable", "red"),
                    pill(f"Sheet `{sheet_id}`", "slate"),
                ]
            ),
            unsafe_allow_html=True,
        )

        if ready:
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
    with card(
        "3",
        "Connected Accounts & Automated Webhooks",
        "Every supported platform forwards to the same single endpoint.",
    ):
        st.markdown(
            "Configure the forwarder once and the agent logs applications and status "
            "updates with no further interaction."
        )

        render_platform_pills()

        st.markdown('<div class="ap-section-label">Webhook Endpoint</div>', unsafe_allow_html=True)
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

        with st.expander("Email auto-forwarding (Zapier / Make)"):
            st.markdown(
                f"""
                1. Create a Zap or scenario with an email trigger matching your job
                   notifications (LinkedIn, Indeed, Glassdoor, Upwork, Workday, Greenhouse).
                2. Add a **Webhook / HTTP POST** action pointing at `{WEBHOOK_URL}`.
                3. Map your sheet ID to `sheet_id` and the email body to `resume_text`.
                4. Set the prefix to `Platform:` so classification stays accurate.
                """
            )

        with st.expander("Gmail + Apps Script"):
            st.markdown(
                f"""
                1. Label job notifications with a filter, for example `job-updates`.
                2. Run a time-driven Apps Script that reads the label and POSTs the message
                   body to the endpoint above: `{WEBHOOK_URL}`.
                """
            )

        with st.expander("Background browser extension or local script"):
            st.markdown(
                f"""
                1. Run an extension or Playwright script on your own machine.
                2. Watch the notification pages for {", ".join(PLATFORMS[:8])}.
                3. POST the visible text to `{WEBHOOK_URL}` whenever new content appears.
                """
            )


def render_activity_section(result: FetchResult | None) -> None:
    with card(
        "4",
        "Agent Activity & Status Updates Log",
        f"Most recent entries synced to your sheet, refreshed every {REFRESH_SECONDS}s.",
    ):
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
    st.set_page_config(page_title=BRAND_TITLE, page_icon="🤖", layout="centered")
    inject_styles()
    render_brand_header()

    sheet_id, api_key = render_configuration_section()
    if sheet_id:
        render_live_sections(sheet_id, api_key)
    else:
        render_integration_section()


if __name__ == "__main__":
    main()