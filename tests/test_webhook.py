import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api import routes
from main import app
from services.sheet_service import JobApplication


class WebhookSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_application_webhook_uses_requested_client_sheet(self) -> None:
        application = JobApplication(
            company_name="Acme",
            role="Software Engineer",
            platform_used="Typeform",
            status="Pending",
        )
        transport = httpx.ASGITransport(app=app)

        with (
            patch.object(routes, "classify_application", return_value=application),
            patch.object(routes, "update_or_append_application", return_value=True) as write_application,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/webhook/application",
                    json={
                        "sheet_id": "client-sheet-id",
                        "applicant_data": {
                            "company": "Acme",
                            "role": "Software Engineer",
                            "platform_used": "Typeform",
                        },
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "processed")
        self.assertTrue(response.json()["sheet_updated"])
        write_application.assert_called_once_with(application, "client-sheet-id")


if __name__ == "__main__":
    unittest.main()