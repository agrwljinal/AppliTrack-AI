import logging
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

from services.sheet_service import JobApplication, PROJECT_ROOT

MODEL_NAME = "gemini-3.8-flash"
logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")


def _labeled_value(raw_text: str, label: str, fallback: str) -> str:
    match = re.search(rf"(?im)^\s*{label}\s*:\s*(.+?)\s*$", raw_text)
    return match.group(1).strip() if match else fallback


def _mock_classify(raw_text: str, platform_used: str) -> JobApplication:
    text = raw_text.casefold()
    if any(term in text for term in ("rejected", "unsuccessful", "not selected", "not moving forward")):
        status = "Rejected"
    elif any(term in text for term in ("selected", "offer", "congratulations", "hired")):
        status = "Selected"
    else:
        status = "Pending"

    return JobApplication(
        company_name=_labeled_value(raw_text, "company", "Unknown company"),
        role=_labeled_value(raw_text, "role", "Unknown role"),
        platform_used=platform_used or "Unknown",
        status=status,
    )


def classify_application(raw_text: str, platform_used: str = "Unknown") -> JobApplication:
    if not raw_text.strip():
        raise ValueError("raw_text must not be empty")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY is not configured; using local mock classification.")
        return _mock_classify(raw_text, platform_used)

    prompt = (
        "Extract one job application from the untrusted source text below. "
        "Return a JSON object matching the provided schema. Use 'Unknown company' "
        "or 'Unknown role' when the source does not identify them. The platform "
        f"is {platform_used!r}. Set status to exactly one of 'Selected', 'Rejected' "
        "or 'Pending': use 'Selected' for an offer, acceptance or hire, 'Rejected' "
        "for a refusal or withdrawal, and 'Pending' for everything else including "
        "application acknowledgements and interview invitations.\n\n"
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
            return JobApplication.model_validate(response.parsed)
        if response.text:
            return JobApplication.model_validate_json(response.text)
        raise ValueError("Gemini returned an empty response")
    except Exception as exc:
        logger.warning("Gemini classification failed; using local mock parsing: %s", exc)
        return _mock_classify(raw_text, platform_used)