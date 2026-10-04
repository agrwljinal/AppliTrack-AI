import logging
import os
import re
from datetime import date

from dotenv import load_dotenv
from google import genai
from google.genai import types

from services.parser import (
    STATUS_PENDING,
    infer_status,
    is_meaningful,
    parse_email,
)
from services.sheet_service import JobApplication, PROJECT_ROOT

MODEL_NAME = "gemini-3.8-flash"
logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")


def _labeled_value(raw_text: str, label: str, fallback: str) -> str:
    match = re.search(rf"(?im)^\s*{label}\s*:\s*(.+?)\s*$", raw_text)
    return match.group(1).strip() if match else fallback


def _mock_classify(raw_text: str, platform_used: str, subject: str, sender: str) -> JobApplication:
    hints = parse_email(subject, sender, raw_text)

    return JobApplication(
        company_name=hints.company or _labeled_value(raw_text, "company", "Unknown company"),
        role=hints.role or _labeled_value(raw_text, "role", "Unknown role"),
        platform_used=platform_used or "Unknown",
        status=hints.status,
        date_updated=hints.sent_on or date.today(),
    )


def _apply_fallbacks(application: JobApplication, subject: str, sender: str, body: str) -> JobApplication:
    """Fill gaps in a model result from the platform-agnostic parser.

    A value the model actually identified is never overwritten.
    """
    needs_company = not is_meaningful(application.company_name)
    needs_role = not is_meaningful(application.role)
    needs_status = application.status == STATUS_PENDING

    if not (needs_company or needs_role or needs_status):
        return application

    hints = parse_email(subject, sender, body)
    updates: dict[str, object] = {}

    if needs_company and hints.company:
        updates["company_name"] = hints.company
    if needs_role and hints.role:
        updates["role"] = hints.role
    if needs_status:
        # "Pending" is the model's catch-all for a mail it could not read. The
        # parser only escalates on explicit refusal or offer wording, so it never
        # overrides a status the model chose deliberately.
        updates["status"] = infer_status(subject, body)

    return application.model_copy(update=updates)


def classify_application(
    raw_text: str,
    platform_used: str = "Unknown",
    subject: str = "",
    sender: str = "",
) -> JobApplication:
    """Turn one notification into a sheet row.

    `subject` and `sender` are optional so existing callers keep working, but they
    materially improve accuracy: the subject line usually names the role and the
    employer, and the sender display name is the last-resort company.
    """
    if not raw_text.strip():
        raise ValueError("raw_text must not be empty")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY is not configured; using local mock classification.")
        return _mock_classify(raw_text, platform_used, subject, sender)

    header = ""
    if subject or sender:
        header = f"Subject: {subject or '(none)'}\nFrom: {sender or '(none)'}\n\n"

    prompt = (
        "Extract one job application from the untrusted source text below. "
        "Return a JSON object matching the provided schema. Use 'Unknown company' "
        "or 'Unknown role' when the source does not identify them. The platform "
        f"is {platform_used!r}. Set status to exactly one of 'Applied', 'Selected', "
        "'Rejected' or 'Pending': use 'Applied' for an acknowledgement that the "
        "application was submitted or received, 'Selected' for an offer, acceptance "
        "or hire, 'Rejected' for a refusal or withdrawal, and 'Pending' only when the "
        "source does not say how the application fared.\n\n"
        f"{header}"
        f"Source text:\n{raw_text}"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JobApplication,
            ),
        )
        if response.parsed is not None:
            application = JobApplication.model_validate(response.parsed)
        elif response.text:
            application = JobApplication.model_validate_json(response.text)
        else:
            raise ValueError("Gemini returned an empty response")
    except Exception as exc:
        logger.warning("Gemini classification failed; using local mock parsing: %s", exc)
        return _mock_classify(raw_text, platform_used, subject, sender)

    return _apply_fallbacks(application, subject, sender, raw_text)
