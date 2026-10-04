import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as dashboard
from api import routes
from services import gmail_service as gs
from services.sheet_service import HEADERS

SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
ROOT = "https://applitrack-ai.onrender.com"
CALLBACK = "https://applitrack-ai.onrender.com/auth/google/callback"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CREDS = {
    "GOOGLE_CLIENT_ID": "123456.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-secret",
    "GOOGLE_REDIRECT_URI": CALLBACK,
}
OAUTH_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "GOOGLE_SHEET_ID")


def set_oauth_env(**overrides: str) -> None:
    for name in OAUTH_ENV:
        os.environ.pop(name, None)
    os.environ.update({**CREDS, **overrides})


class FakeQueryParams(dict):
    """Stands in for st.query_params, including .clear()."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cleared = False

    def clear(self) -> None:
        self.cleared = True
        super().clear()


class GoogleAuthUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        set_oauth_env()

    def test_points_straight_at_google(self) -> None:
        parsed = urlparse(dashboard.get_google_auth_url(SHEET))

        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/auth")

    def test_requests_only_the_gmail_readonly_scope(self) -> None:
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertEqual(query["scope"], [GMAIL_READONLY])
        self.assertEqual(query["access_type"], ["offline"])

    def test_redirect_uri_is_unchanged_so_google_still_accepts_it(self) -> None:
        """The URI registered with Google must not change; only the handler moves."""
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertEqual(query["redirect_uri"], [CALLBACK])

    def test_state_carries_the_sheet_id(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]

        self.assertEqual(gs.read_oauth_state(state), SHEET)

    def test_consent_url_sends_no_pkce_challenge(self) -> None:
        """PKCE on by default would break the exchange; the verifier cannot survive
        the redirect, so Google would reject the code with invalid_grant."""
        set_oauth_env()
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertNotIn("code_challenge", query)
        self.assertNotIn("code_challenge_method", query)

    def test_shared_flow_factory_disables_pkce(self) -> None:
        flow = gs.build_oauth_flow(ROOT)

        self.assertFalse(flow.autogenerate_code_verifier)
        self.assertIsNone(flow.code_verifier)

    def test_auth_url_generator_has_a_single_flow_implementation(self) -> None:
        """Divergent Flow construction is what caused the PKCE mismatch."""
        import inspect

        for func in (dashboard.get_google_auth_url, gs.authorization_url, routes.google_login):
            with self.subTest(func=func.__name__):
                self.assertNotIn("Flow.from_client_config", inspect.getsource(func))

        self.assertIn("autogenerate_code_verifier=False", inspect.getsource(gs.build_oauth_flow))

    def test_redirect_uri_follows_the_env_var_immediately(self) -> None:
        """Nothing may memoise the value, so an env change takes effect at once."""
        seen = []
        for value in (ROOT, "https://staging.applitrack-ai.onrender.com", ROOT):
            set_oauth_env(GOOGLE_REDIRECT_URI=value)
            query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)
            seen.append(query["redirect_uri"][0])

        self.assertEqual(
            seen,
            [
                ROOT,
                "https://staging.applitrack-ai.onrender.com",
                ROOT,
            ],
        )

    def test_blank_env_value_falls_back_to_the_root_url(self) -> None:
        for raw in ("", "   "):
            with self.subTest(raw=raw):
                set_oauth_env(GOOGLE_REDIRECT_URI=raw)
                query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)
                self.assertEqual(query["redirect_uri"], [ROOT])

    def test_redirect_uri_never_carries_a_loopback_or_extra_path(self) -> None:
        set_oauth_env()
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        url = dashboard.get_google_auth_url(SHEET)
        redirect_uri = parse_qs(urlparse(url).query)["redirect_uri"][0]

        self.assertNotIn("127.0.0.1", url)
        self.assertNotIn("localhost", url)
        self.assertEqual(urlparse(redirect_uri).path, "")
        self.assertEqual(urlparse(url).netloc, "accounts.google.com")

    def test_an_explicitly_configured_callback_path_is_still_honoured(self) -> None:
        set_oauth_env()
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertEqual(query["redirect_uri"], [CALLBACK])

    def test_state_is_fresh_on_every_call(self) -> None:
        """A cached URL would reuse one CSRF nonce across every visitor."""
        set_oauth_env()
        states = [
            parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
            for _ in range(3)
        ]

        self.assertEqual(len(set(states)), 3)

    def test_auth_url_generator_is_not_cached(self) -> None:
        """st.cache_data is global across sessions; it must never wrap this."""
        for cached in (dashboard.st.cache_data, dashboard.st.cache_resource):
            with self.subTest(cache=type(cached).__name__):
                self.assertIsNot(getattr(dashboard, "get_google_auth_url"), cached)
                unwrapped = getattr(dashboard.get_google_auth_url, "__wrapped__", None)
                self.assertIsNone(unwrapped, "the URL builder must not be a cached wrapper")

    def test_falls_back_to_the_root_redirect_uri(self) -> None:
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertEqual(query["redirect_uri"], [ROOT])

    def test_default_redirect_uri_matches_the_token_exchange(self) -> None:
        """A mismatch between the two is what triggers Google's redirect_uri_mismatch."""
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        query = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)

        self.assertEqual(query["redirect_uri"][0], gs.redirect_uri())

    def test_root_callback_query_params_are_redeemed(self) -> None:
        """The root URL supplies the same ?code=/?state= params as any sub-path."""
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        saved = {}

        def fake_exchange(code, received_state):
            saved["code"] = code
            saved["sheet"] = gs.read_oauth_state(received_state)
            return "user@gmail.com"

        query_params = FakeQueryParams({"code": "root-code", "state": state})
        rerun = MagicMock()
        error = MagicMock()

        with patch.object(dashboard, "exchange_code_for_tokens", side_effect=fake_exchange), \
             patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "error", error), \
             patch.object(dashboard.st, "success", MagicMock()), \
             patch.object(dashboard.st, "rerun", rerun):
            dashboard._complete_oauth_from_query()

        self.assertFalse(error.called)
        self.assertEqual(saved["code"], "root-code")
        self.assertEqual(saved["sheet"], SHEET)
        self.assertTrue(query_params.cleared)
        self.assertTrue(rerun.called)

    def test_missing_credentials_raise_a_readable_error(self) -> None:
        for missing in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
            set_oauth_env(**{missing: ""})
            with self.subTest(missing=missing):
                with self.assertRaises(gs.OAuthConfigurationError) as caught:
                    dashboard.get_google_auth_url(SHEET)
                self.assertIn(missing, str(caught.exception))


class TokenExchangeWithoutStoredVerifierTest(unittest.TestCase):
    """The consent URL and the code redemption happen in different requests."""

    def setUp(self) -> None:
        set_oauth_env()

    def test_fresh_flow_redeems_the_code_without_a_code_verifier(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        captured = {}

        class FakeCredentials:
            token = "ya29.fake"
            refresh_token = "1//fake-refresh"
            token_uri = "https://oauth2.googleapis.com/token"
            client_id = CREDS["GOOGLE_CLIENT_ID"]
            client_secret = CREDS["GOOGLE_CLIENT_SECRET"]
            scopes = [GMAIL_READONLY]

        def fake_fetch_token(self, **kwargs):
            captured["code"] = kwargs.get("code")
            captured["state"] = kwargs.get("state")
            captured["verifier"] = getattr(self, "code_verifier", "unset")
            type(self).credentials = FakeCredentials()

        with patch.object(gs.Flow, "fetch_token", fake_fetch_token), \
             patch.object(gs, "_fetch_gmail_address", return_value="user@gmail.com"), \
             patch.object(gs, "save_connected_account") as save:
            email = gs.exchange_code_for_tokens("the-auth-code", state)

        self.assertEqual(email, "user@gmail.com")
        self.assertEqual(captured["code"], "the-auth-code")
        self.assertEqual(captured["state"], state)
        self.assertIsNone(captured["verifier"], "no verifier may be demanded or sent")
        self.assertEqual(save.call_args[0][1], SHEET, "token is bound to the signed sheet")


class CallbackCompletionTest(unittest.TestCase):
    """The dashboard receives the callback itself and must redeem it exactly once."""

    def setUp(self) -> None:
        set_oauth_env()
        _reset_notice()

    def _run(self, params, exchange=None, sheet_config=None):
        saved = {}

        def fake_exchange(code, state):
            saved["code"] = code
            saved["state"] = state
            saved["sheet"] = gs.read_oauth_state(state)
            return "user@gmail.com"

        query_params = FakeQueryParams(params)
        error = MagicMock()
        success = MagicMock()
        rerun = MagicMock()

        def read_config():
            return sheet_config

        with patch.object(dashboard, "read_oauth_state", gs.read_oauth_state), \
             patch.object(dashboard, "exchange_code_for_tokens", exchange or fake_exchange), \
             patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "error", error), \
             patch.object(dashboard.st, "success", success), \
             patch.object(dashboard.st, "rerun", rerun):
            if sheet_config is not None:
                with patch.object(dashboard, "CONFIG_PATH", _tmp_config(sheet_config)):
                    dashboard._complete_oauth_from_query()
            else:
                dashboard._complete_oauth_from_query()

        return saved, query_params, error, success, rerun

    def test_exchanges_the_code_and_reloads(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        saved, params, error, success, rerun = self._run({"code": "code-1", "state": state})

        self.assertEqual(saved["code"], "code-1")
        self.assertEqual(saved["sheet"], SHEET)
        self.assertTrue(params.cleared, "query params must be cleared so the code is not reused")
        self.assertEqual(dict(params), {})
        self.assertTrue(rerun.called)

    def test_stores_the_success_notice_for_the_next_render(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        _reset_notice()

        saved, params, error, success, rerun = self._run({"code": "code-1", "state": state})
        notice = dashboard.st.session_state.get("oauth_notice")

        self.assertIsNotNone(notice, "a notice must survive the reload")
        self.assertEqual(notice[0], "success")
        self.assertIn("user@gmail.com", notice[1])
        self.assertTrue(rerun.called)

    def test_rejects_a_tampered_state(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        saved, params, error, success, rerun = self._run(
            {"code": "code-1", "state": state[:-4] + "aaaa"}
        )

        self.assertEqual(saved, {}, "the code must not be exchanged on a bad state")
        self.assertTrue(error.called, "the failure must be shown immediately")
        self.assertIn("signature", error.call_args[0][0].lower())
        self.assertTrue(params.cleared, "a dead code must not be left in the URL")
        self.assertFalse(rerun.called, "a failure must not trigger a reload loop")

    def test_rejects_a_missing_state(self) -> None:
        saved, params, error, success, rerun = self._run({"code": "code-1"})

        self.assertEqual(saved, {})
        self.assertTrue(error.called)
        self.assertIn("state", error.call_args[0][0].lower())
        self.assertFalse(rerun.called)

    def test_reports_a_failed_exchange(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        saved, params, error, success, rerun = self._run(
            {"code": "code-1", "state": state},
            exchange=MagicMock(side_effect=RuntimeError("token endpoint said no")),
        )

        self.assertTrue(error.called)
        self.assertIn("token endpoint said no", error.call_args[0][0])
        self.assertFalse(rerun.called)

    def test_the_same_code_is_never_redeemed_twice(self) -> None:
        """Streamlit re-runs on every interaction and on a timer; the URL may still
        carry ?code= when the clear has not reached the browser yet."""
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        calls = []

        def counting(code, state_arg):
            calls.append(code)
            return "user@gmail.com"

        class StubbornParams(dict):
            def clear(self):
                pass  # models the frontend not applying the clear yet

        query_params = StubbornParams({"code": "code-1", "state": state})
        rerun = MagicMock()

        with patch.object(dashboard, "exchange_code_for_tokens", side_effect=counting), \
             patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "error", MagicMock()), \
             patch.object(dashboard.st, "success", MagicMock()), \
             patch.object(dashboard.st, "rerun", rerun):
            for _ in range(10):
                dashboard.render_callback_notice()

        self.assertEqual(calls, ["code-1"], "one redemption across ten script runs")
        self.assertEqual(rerun.call_count, 1, "at most one reload")

    def test_a_new_authorization_code_is_still_processed(self) -> None:
        state = parse_qs(urlparse(dashboard.get_google_auth_url(SHEET)).query)["state"][0]
        calls = []
        query_params = FakeQueryParams({"code": "code-one", "state": state})

        with patch.object(dashboard, "exchange_code_for_tokens",
                          side_effect=lambda c, s: calls.append(c) or "user@gmail.com"), \
             patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "error", MagicMock()), \
             patch.object(dashboard.st, "success", MagicMock()), \
             patch.object(dashboard.st, "rerun", MagicMock()):
            dashboard._complete_oauth_from_query()
            query_params["code"] = "code-two"
            query_params["state"] = state
            dashboard._complete_oauth_from_query()

        self.assertEqual(calls, ["code-one", "code-two"])


class CallbackNoticeTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_notice()

    def test_google_error_param_is_surfaced_and_cleared(self) -> None:
        query_params = FakeQueryParams({"error": "access_denied"})
        error = MagicMock()

        with patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "error", error):
            dashboard.render_callback_notice()

        self.assertTrue(error.called)
        self.assertIn("access_denied", error.call_args[0][0])
        self.assertTrue(query_params.cleared)

    def test_legacy_auth_success_notice_still_works(self) -> None:
        query_params = FakeQueryParams({"auth": "success"})
        success = MagicMock()

        with patch.object(dashboard.st, "query_params", query_params), \
             patch.object(dashboard.st, "success", success), \
             patch.object(dashboard.st, "error", MagicMock()):
            dashboard.render_callback_notice()

        self.assertTrue(success.called)

    def test_nothing_happens_without_callback_params(self) -> None:
        success = MagicMock()
        error = MagicMock()

        with patch.object(dashboard.st, "query_params", FakeQueryParams({})), \
             patch.object(dashboard.st, "success", success), \
             patch.object(dashboard.st, "error", error):
            dashboard.render_callback_notice()

        self.assertFalse(success.called)
        self.assertFalse(error.called)


def _reset_notice() -> None:
    """Drop leftover callback state so each test starts on a clean session."""
    dashboard.st.session_state.pop("oauth_notice", None)
    dashboard.st.session_state.pop("redeemed_oauth_code", None)


def _tmp_config(sheet_id):
    import tempfile

    path = Path(tempfile.mkdtemp()) / "applitrack_config.json"
    path.write_text(json.dumps({"sheet_id": sheet_id}), encoding="utf-8")
    return path


class ConnectButtonTest(unittest.TestCase):
    """Section 2 must render the button with a locally generated URL."""

    def setUp(self) -> None:
        set_oauth_env()

    def _render(self, credentials_ok=True):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=90).run()
        at.text_input[0].set_value(SHEET)

        ws = MagicMock()
        ws.get_all_values.return_value = [list(HEADERS), ["Acme", "SWE", "LinkedIn", "Pending", "2026-10-03"]]
        status = {
            "configured": True, "oauth_configured": True, "connected": False,
            "email": None, "sheet_id": SHEET, "connected_emails": [], "error": None,
        }

        def fake_exchange(code, state):
            raise AssertionError("must not exchange a code while rendering")

        with patch.object(gs, "connection_status", return_value=status), \
             patch.object(gs, "exchange_code_for_tokens", side_effect=fake_exchange), \
             patch("services.sheet_service.read_recent_applications", return_value=[{"a": 1}]), \
             patch("services.sheet_service._get_worksheet", return_value=ws):
            if not credentials_ok:
                os.environ["GOOGLE_CLIENT_ID"] = ""
            at.run()

        return at

    def test_connect_button_targets_google_directly(self) -> None:
        at = self._render()

        self.assertEqual(len(at.exception), 0, [str(e.value) for e in at.exception])
        links = [b.proto.url for b in at.get("link_button")]
        self.assertTrue(links, "the connect button must render")
        self.assertTrue(
            all("accounts.google.com" in url for url in links),
            f"button must not depend on the API hop: {links}",
        )

    def test_missing_credentials_warn_instead_of_breaking(self) -> None:
        at = self._render(credentials_ok=False)

        self.assertEqual(len(at.exception), 0, [str(e.value) for e in at.exception])
        self.assertIn(
            "Google OAuth credentials missing on server.",
            [w.value for w in at.warning],
        )


if __name__ == "__main__":
    unittest.main()
