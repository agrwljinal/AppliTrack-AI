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
from services import gmail_service

STATUS_KEYS = {
    "configured",
    "connected",
    "connected_emails",
    "email",
    "error",
    "oauth_configured",
    "sheet_id",
}


async def _get_status(client: httpx.AsyncClient, **params):
    response = await client.get("/auth/google/status", params=params)
    return response


class GoogleStatusRouteTest(unittest.IsolatedAsyncioTestCase):
    """The status route must delegate to the shared helper and never 500 on store problems."""

    async def test_route_delegates_to_shared_helper(self) -> None:
        expected = {key: None for key in STATUS_KEYS}
        transport = httpx.ASGITransport(app=app)

        with patch.object(routes, "connection_status", return_value=expected) as shared:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await _get_status(client, sheet_id="sheet-1")

        self.assertEqual(response.status_code, 200, response.text)
        shared.assert_called_once_with("sheet-1")

    async def test_route_keeps_response_contract(self) -> None:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await _get_status(client, sheet_id="  sheet-1  ")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(set(response.json()), STATUS_KEYS)
        self.assertEqual(response.json()["sheet_id"], "sheet-1")

    async def test_undecryptable_token_store_returns_error_not_500(self) -> None:
        """A corrupt/mismatched token store must surface as `error`, not a server error."""
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        def explode():
            raise gmail_service.OAuthConfigurationError(
                "Stored Gmail tokens cannot be decrypted. Check APPLITRACK_TOKEN_KEY."
            )

        with patch.object(gmail_service, "read_store", side_effect=explode):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await _get_status(client, sheet_id="sheet-1")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["connected"])
        self.assertFalse(body["connected_emails"])
        self.assertIsNone(body["email"])
        self.assertIn("cannot be decrypted", body["error"])

    async def test_api_key_is_still_enforced(self) -> None:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        with patch.dict("os.environ", {"APPLITRACK_API_KEY": "expected-key"}):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                rejected = await _get_status(client, sheet_id="sheet-1")
                accepted = await _get_status(client, sheet_id="sheet-1", client_api_key="expected-key")

        self.assertEqual(rejected.status_code, 401, rejected.text)
        self.assertEqual(accepted.status_code, 200, accepted.text)


class DashboardUsesInProcessServicesTest(unittest.TestCase):
    """The dashboard must not call the backend over loopback HTTP."""

    def setUp(self) -> None:
        self.source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    def test_no_loopback_http_calls(self) -> None:
        for forbidden in ("127.0.0.1", "localhost:8000", "APPLICATIONS_URL", "GOOGLE_STATUS_URL"):
            self.assertNotIn(forbidden, self.source, f"app.py still references {forbidden}")

    def test_imports_services_in_process(self) -> None:
        self.assertIn("from services.gmail_service import connection_status", self.source)
        self.assertIn("read_recent_applications", self.source)

    def test_public_oauth_link_is_preserved(self) -> None:
        import app as dashboard

        self.assertEqual(
            dashboard.GOOGLE_LOGIN_URL,
            "https://applitrack-ai.onrender.com/auth/google/login",
            "the public OAuth button must stay public",
        )


if __name__ == "__main__":
    unittest.main()