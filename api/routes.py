import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from services.ai_classifier import classify_application
from services.sheet_service import update_or_append_application

router = APIRouter()


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


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "AppliTrack AI"}