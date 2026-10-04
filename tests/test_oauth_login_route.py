import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.routes import router
from main import app
from services.gmail_service import OAuthConfigurationError, build_oauth_state, read_oauth_state

OAUTH_ENV = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "GOOGLE_SHEET_ID",
)
CREDS = {
    "GOOGLE_CLIENT_ID": "123456.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-secret",
    "GOOGLE_REDIRECT_URI": "https://applitrack-ai.onrender.com/auth/google/callback",
}
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class OAuthLoginRouteTest(unittest.IsolatedAsyncioTestCase):
    """The login route must redirect with an explicit 307, or explain itself in JSON."""

    def setUp(self) -> None:
        self.transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    def env(self, **overrides: str) -> None:
        for name in OAUTH_ENV:
            os.environ.pop(name, None)
        os.environ.update({**CREDS, **overrides})

    async def login(self, **params) -> httpx.Response:
        async with httpx.AsyncClient(transport=self.transport, base_url="http://testserver") as client:
            return await client.get(
                "/auth/google/login", params=params, follow_redirects=False
            )

    async def test_returns_explicit_307_redirect_to_google(self) -> None:
        self.env()
        response = await self.login(sheet_id="sheet-1")

        self.assertEqual(response.status_code, 307)
        location = urlparse(response.headers["location"])
        query = parse_qs(location.query)
        self.assertEqual(location.netloc, "accounts.google.com")
        self.assertEqual(location.path, "/o/oauth2/auth")
        self.assertEqual(query["client_id"], [CREDS["GOOGLE_CLIENT_ID"]])
        self.assertEqual(query["redirect_uri"], [CREDS["GOOGLE_REDIRECT_URI"]])
        self.assertEqual(query["access_type"], ["offline"])

    async def test_requests_only_the_gmail_readonly_scope(self) -> None:
        self.env()
        response = await self.login(sheet_id="sheet-1")
        query = parse_qs(urlparse(response.headers["location"]).query)

        self.assertEqual(query["scope"], [GMAIL_READONLY])

    async def test_state_is_signed_for_the_requested_sheet(self) -> None:
        self.env()
        response = await self.login(sheet_id="sheet-1")
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]

        self.assertEqual(read_oauth_state(state), "sheet-1")

        tampered = state[:-4] + ("aaaa" if not state.endswith("aaaa") else "bbbb")
        with self.assertRaises(ValueError):
            read_oauth_state(tampered)

    async def test_missing_credentials_return_readable_json_error(self) -> None:
        self.env(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
        response = await self.login(sheet_id="sheet-1")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        error = response.json()["error"]
        self.assertIn("GOOGLE_CLIENT_ID", error)
        self.assertIn("GOOGLE_CLIENT_SECRET", error)

    async def test_missing_sheet_id_returns_readable_json_error(self) -> None:
        self.env()
        response = await self.login()

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("sheet_id", response.json()["error"])

    async def test_falls_back_to_default_sheet_and_redirect_uri(self) -> None:
        self.env(GOOGLE_SHEET_ID="default-sheet-42")
        response = await self.login()
        query = parse_qs(urlparse(response.headers["location"]).query)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(read_oauth_state(query["state"][0]), "default-sheet-42")

        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        response = await self.login(sheet_id="sheet-1")
        query = parse_qs(urlparse(response.headers["location"]).query)
        self.assertEqual(
            query["redirect_uri"], ["https://applitrack-ai.onrender.com"]
        )

    async def test_unexpected_failure_is_reported_as_json_not_a_crash(self) -> None:
        self.env()
        with patch("api.routes.Flow.from_client_config", side_effect=RuntimeError("boom")):
            response = await self.login(sheet_id="sheet-1")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"], "boom")

    async def test_dashboard_button_url_resolves_to_a_redirect(self) -> None:
        self.env()
        async with httpx.AsyncClient(transport=self.transport, base_url="http://testserver") as client:
            response = await client.get(
                "/auth/google/login?sheet_id=1AbCdEfG", follow_redirects=False
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            urlparse(response.headers["location"]).netloc, "accounts.google.com"
        )

    async def test_callback_still_redirects_back_to_the_dashboard(self) -> None:
        """The starlette RedirectResponse swap must not break the callback leg."""
        with patch("api.routes.exchange_code_for_tokens", return_value="user@gmail.com"):
            async with httpx.AsyncClient(
                transport=self.transport, base_url="http://testserver"
            ) as client:
                response = await client.get(
                    "/auth/google/callback",
                    params={"code": "auth-code", "state": "state-token"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 307)
        self.assertIn("auth=success", response.headers["location"])


class CallbackHandoffTest(unittest.IsolatedAsyncioTestCase):
    """The API hands an unredeemable code to the dashboard without leaking or losing state."""

    def setUp(self) -> None:
        for name in OAUTH_ENV:
            os.environ.pop(name, None)
        os.environ["STREAMLIT_UI_URL"] = "https://applitrack-ai.onrender.com"
        self.state = build_oauth_state("sheet-abc")

    async def _callback(self, **patch_kwargs):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        with patch("api.routes.exchange_code_for_tokens", **patch_kwargs):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get(
                    "/auth/google/callback",
                    params={"code": "api-code", "state": self.state},
                    follow_redirects=False,
                )

    async def test_successful_exchange_redirects_without_leaking_the_code(self) -> None:
        response = await self._callback(return_value="user@gmail.com")

        self.assertEqual(response.status_code, 307)
        self.assertIn("auth=success", response.headers["location"])
        self.assertNotIn("code=", response.headers["location"])

    async def test_unredeemable_code_is_handed_to_the_dashboard_with_state(self) -> None:
        for failure in (
            OAuthConfigurationError("GOOGLE_CLIENT_ID is not set"),
            RuntimeError("api process is broken"),
        ):
            with self.subTest(failure=type(failure).__name__):
                response = await self._callback(side_effect=failure)

                self.assertEqual(response.status_code, 307, response.text)
                location = response.headers["location"]
                self.assertEqual(urlparse(location).netloc, "applitrack-ai.onrender.com")
                query = parse_qs(urlparse(location).query)
                self.assertEqual(query["code"], ["api-code"])
                self.assertEqual(
                    query["state"], [self.state], "state carries the signed sheet binding"
                )
                self.assertEqual(read_oauth_state(query["state"][0]), "sheet-abc")

    async def test_invalid_code_reports_an_error_instead_of_a_500(self) -> None:
        response = await self._callback(side_effect=ValueError("code already redeemed"))

        self.assertEqual(response.status_code, 307)
        query = parse_qs(urlparse(response.headers["location"]).query)
        self.assertTrue(query["auth"][0].startswith("error:"), query)
        self.assertIn("already redeemed", query["auth"][0])
        self.assertNotIn("code=", response.headers["location"])


class RouterMountTest(unittest.TestCase):
    def test_login_route_is_exposed_on_the_main_app(self) -> None:
        self.assertIn(
            "/auth/google/login", {getattr(route, "path", None) for route in router.routes}
        )
        self.assertIn("/auth/google/login", app.openapi()["paths"])

    def test_openapi_documents_the_redirect(self) -> None:
        operation = app.openapi()["paths"]["/auth/google/login"]["get"]

        for code in ("307", "400", "503"):
            self.assertIn(code, operation["responses"])


if __name__ == "__main__":
    unittest.main()