import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlencode

import streamlit as st

from services.gmail_service import connection_status
from services.sheet_service import read_recent_applications

PUBLIC_BASE_URL = "https://applitrack-ai.onrender.com"
GOOGLE_LOGIN_URL = f"{PUBLIC_BASE_URL}/auth/google/login"
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
)
CONFIG_PATH = Path(__file__).resolve().parent / "applitrack_config.json"
API_KEY_ENV_VAR = "APPLITRACK_API_KEY"
SHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
RAW_SHEET_ID_PATTERN = re.compile(r"^[a-zA-Z0-9-_]{10,}$")
REFRESH_SECONDS = 30
LOG_LIMIT = 10

FetchState = Literal["ok", "empty", "error"]

BRAND_TITLE = "🤖 AppliTrack AI"
BRAND_TAGLINE = "Autonomous 1-Click Gmail Sync for Multi-Platform Job Applications"

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
    font-size: 2.45rem;
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
  .stButton > button[kind="primary"], .stLinkButton > a {
    border-radius: 10px;
    font-weight: 600;
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
          {pill("Read-Only Gmail Access", "green", dotted=True)}
          {pill("Zero Configuration", "blue")}
          {pill(f"Refreshes every {REFRESH_SECONDS}s", "slate")}
        </div>
        """,
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


def fetch_applications(sheet_id: str, limit: int) -> FetchResult:
    """Read the recent application log straight from the sheet service."""
    try:
        rows = read_recent_applications(sheet_id, limit)
    except Exception as exc:
        return FetchResult("error", [], f"Could not read the Google Sheet: {exc}")

    if rows is None:
        return FetchResult(
            "error",
            [],
            "The service could not read your Google Sheet. Confirm it is shared as Editor "
            f"with {SERVICE_ACCOUNT_EMAIL}.",
        )

    if not rows:
        return FetchResult("empty", [], "No applications have been logged to this sheet yet.")

    return FetchResult("ok", rows, f"{len(rows)} recent application(s) read from your sheet.")


def fetch_gmail_status(sheet_id: str) -> tuple[dict[str, Any] | None, str]:
    """Return the Gmail connection status for a sheet, in-process."""
    try:
        status = connection_status(sheet_id)
    except Exception as exc:
        return None, f"Backend status check failed: {exc}"

    return status, status.get("error") or ""


def render_callback_notice() -> None:
    """Surface the redirect result the backend sends back after the OAuth handshake."""
    outcome = st.query_params.get("auth", "")
    if not outcome:
        return

    if outcome == "success":
        st.success("Gmail connected. The agent is now watching your inbox for job updates.")
    else:
        st.error(f"Gmail connection failed: {outcome.removeprefix('error:')}")


def render_configuration_section() -> str:
    with card(
        "1",
        "One-Time Google Sheet Setup",
        "Tell the agent where to write. Everything else happens automatically.",
    ):
        with st.expander("Grant the service account access to your Google Sheet", expanded=True):
            st.markdown(
                f"""
                AppliTrack AI writes your applications through a shared service account.
                Grant it **Editor** access once:

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

        st.text_input(
            "Client API Key (optional)",
            value=os.getenv(API_KEY_ENV_VAR, ""),
            type="password",
            placeholder="Only needed when APPLITRACK_API_KEY is set on Render",
            help=(
                "The dashboard reads the Google Sheet in-process, so this field is not used "
                "here. It only matters when calling the public API directly. Never written "
                "to disk."
            ),
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

    return detected or load_saved_sheet_id()


def render_gmail_section(sheet_id: str) -> None:
    with card(
        "2",
        "Connect Gmail Account",
        "Grant AppliTrack AI read-only access to search your inbox for job notifications.",
    ):
        if not sheet_id:
            st.info("Save your Google Sheet above to unlock the Gmail connection.")
            return

        status, error = fetch_gmail_status(sheet_id)
        connected = bool(status and status.get("connected"))
        oauth_ready = bool(status and status.get("oauth_configured"))

        if connected:
            badges = [
                pill("🟢 Gmail Connected & Monitoring Inbox", "green", dotted=True),
                pill(f"Account: {status.get('email')}", "blue"),
                pill("Read-only access", "slate"),
            ]
        else:
            badges = [
                pill("🔴 Gmail Disconnected", "red", dotted=True),
                pill("No inbox access yet", "slate"),
            ]

        st.markdown("".join(badges), unsafe_allow_html=True)

        if error:
            st.error(error)

        login_url = f"{GOOGLE_LOGIN_URL}?{urlencode({'sheet_id': sheet_id})}"

        if not oauth_ready:
            st.warning(
                "Google OAuth is not configured on the service yet. An administrator needs to "
                "set the Google client credentials before accounts can be connected."
            )

        if connected:
            st.link_button("Reconnect Account", login_url, type="secondary")
            st.caption(
                "Reconnect to authorize a different Google account. "
                "AppliTrack AI only ever receives read-only inbox access."
            )
        else:
            st.link_button("🔗 Connect Gmail Account", login_url, type="primary")
            st.caption(
                "Signs you in with Google and grants read-only inbox access. "
                "AppliTrack AI can never send, delete, or modify your mail."
            )

        st.markdown(
            '<div class="ap-section-label">Platforms Covered Automatically</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            "".join(pill(platform, "blue") for platform in PLATFORMS),
            unsafe_allow_html=True,
        )
        st.caption(
            "The agent watches your inbox for confirmation and status-update emails from "
            "these platforms, then logs each one to your sheet."
        )


def render_activity_section(result: FetchResult | None) -> None:
    with card(
        "3",
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
def render_live_sections(sheet_id: str) -> None:
    render_gmail_section(sheet_id)
    render_activity_section(
        fetch_applications(sheet_id, LOG_LIMIT) if sheet_id else None
    )


def main() -> None:
    st.set_page_config(page_title="AppliTrack AI", page_icon="\U0001F916", layout="centered")
    inject_styles()
    render_brand_header()
    render_callback_notice()

    render_live_sections(render_configuration_section())


if __name__ == "__main__":
    main()