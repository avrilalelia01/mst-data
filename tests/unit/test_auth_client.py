"""Unit tests for shopee_analyzer.auth.AuthClient.

Covers:
- Token acquisition (get_access_token) with mocked HTTP
- Token refresh (refresh_token) with mocked HTTP
- HMAC-SHA256 signature construction (sign_request and auth-endpoint signing)
- get_valid_token: returns stored token, auto-refreshes near-expiry tokens,
  raises AuthenticationError when no token is stored
- Retry behaviour: exactly 3 attempts at 5-second intervals on connection failure
- Descriptive AuthenticationError on invalid-credentials 4xx responses

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from shopee_analyzer.auth import AuthClient, _AUTH_FAILURE_CODES, _REFRESH_BUFFER_SECONDS
from shopee_analyzer.exceptions import APIConnectionError, AuthenticationError
from shopee_analyzer.models import Configuration, TokenPair
from shopee_analyzer.storage import DataStore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config() -> Configuration:
    return Configuration(
        partner_id=12345,
        partner_key="test-partner-key-secret",
        shop_id=67890,
        data_store_path=":memory:",
        output_dir="./output",
        refresh_schedule="daily",
        default_period_days=30,
    )


@pytest.fixture
def data_store(tmp_path) -> DataStore:
    return DataStore(str(tmp_path / "auth_test.db"))


@pytest.fixture
def client(config, data_store) -> AuthClient:
    return AuthClient(config, data_store)


def _make_token_response(
    access_token: str = "acc_token_abc",
    refresh_token: str = "ref_token_xyz",
    expire_in: int = 14400,
    shop_id: int = 67890,
) -> dict:
    """Build a minimal Shopee token API response body."""
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expire_in": expire_in,
        "shop_id": shop_id,
        "error": "",
        "message": "",
    }


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response with the given JSON body and status."""
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Token acquisition — get_access_token
# ---------------------------------------------------------------------------

class TestGetAccessToken:
    def test_returns_token_pair_on_success(self, client, data_store):
        payload = _make_token_response()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            token = client.get_access_token("auth-code-123")

        assert isinstance(token, TokenPair)
        assert token.access_token == "acc_token_abc"
        assert token.refresh_token == "ref_token_xyz"
        assert token.shop_id == 67890

    def test_expires_at_is_approximately_now_plus_expire_in(self, client):
        expire_in = 14400
        payload = _make_token_response(expire_in=expire_in)
        before = datetime.now(tz=timezone.utc)

        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            token = client.get_access_token("auth-code-123")

        after = datetime.now(tz=timezone.utc)
        expected_min = before + timedelta(seconds=expire_in)
        expected_max = after + timedelta(seconds=expire_in)
        assert expected_min <= token.expires_at <= expected_max

    def test_saves_token_to_data_store(self, client, data_store, config):
        payload = _make_token_response()
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            client.get_access_token("auth-code-123")

        stored = data_store.load_token(config.shop_id)
        assert stored is not None
        assert stored.access_token == "acc_token_abc"

    def test_posts_to_correct_url(self, client):
        payload = _make_token_response()
        with patch.object(client._session, "post", return_value=_mock_response(payload)) as mock_post:
            client.get_access_token("some-code")

        call_url = mock_post.call_args[0][0]
        assert call_url.endswith("/api/v2/auth/token/get")

    def test_includes_auth_code_in_body(self, client):
        payload = _make_token_response()
        auth_code = "my-unique-auth-code"
        with patch.object(client._session, "post", return_value=_mock_response(payload)) as mock_post:
            client.get_access_token(auth_code)

        body = mock_post.call_args[1]["json"]
        assert body["code"] == auth_code

    def test_raises_authentication_error_on_invalid_credentials(self, client):
        error_payload = {
            "error": "error_auth",
            "message": "Invalid partner credentials",
            "access_token": "",
            "refresh_token": "",
        }
        mock_resp = _mock_response(error_payload, status_code=401)
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401")

        with patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(AuthenticationError) as exc_info:
                client.get_access_token("bad-code")

        assert "error_auth" in str(exc_info.value).lower() or "authentication failed" in str(exc_info.value).lower()

    def test_error_message_is_descriptive_on_invalid_credentials(self, client):
        """The error message must identify the failure reason (Req 1.4)."""
        error_payload = {
            "error": "invalid_partner",
            "message": "Partner does not exist",
        }
        mock_resp = _mock_response(error_payload, status_code=403)

        with patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(AuthenticationError) as exc_info:
                client.get_access_token("any-code")

        message = str(exc_info.value)
        # Must mention the specific error code or be otherwise descriptive.
        assert "invalid_partner" in message
        assert len(message) > 20  # More than a terse "auth failed" stub.

    def test_raises_api_connection_error_after_retries_exhausted(self, client):
        with patch.object(
            client._session,
            "post",
            side_effect=requests.ConnectionError("unreachable"),
        ):
            with pytest.raises((APIConnectionError, requests.ConnectionError)):
                client.get_access_token("code")

    def test_error_response_in_200_body_raises_authentication_error(self, client):
        """Shopee API can return error codes inside a 200 body."""
        payload = {
            "error": "invalid_sign",
            "message": "Signature does not match",
            "access_token": "",
            "refresh_token": "",
            "expire_in": 0,
        }
        with patch.object(client._session, "post", return_value=_mock_response(payload, 200)):
            with pytest.raises(AuthenticationError) as exc_info:
                client.get_access_token("code")

        assert "invalid_sign" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Token refresh — refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def test_returns_new_token_pair(self, client):
        payload = _make_token_response(
            access_token="new_acc",
            refresh_token="new_ref",
        )
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            token = client.refresh_token("old-refresh-token")

        assert token.access_token == "new_acc"
        assert token.refresh_token == "new_ref"

    def test_posts_to_correct_url(self, client):
        payload = _make_token_response()
        with patch.object(client._session, "post", return_value=_mock_response(payload)) as mock_post:
            client.refresh_token("some-refresh-token")

        call_url = mock_post.call_args[0][0]
        assert call_url.endswith("/api/v2/auth/access_token/get")

    def test_includes_refresh_token_in_body(self, client):
        payload = _make_token_response()
        rt = "the-refresh-token-value"
        with patch.object(client._session, "post", return_value=_mock_response(payload)) as mock_post:
            client.refresh_token(rt)

        body = mock_post.call_args[1]["json"]
        assert body["refresh_token"] == rt

    def test_stores_refreshed_token(self, client, data_store, config):
        payload = _make_token_response(access_token="refreshed_access")
        with patch.object(client._session, "post", return_value=_mock_response(payload)):
            client.refresh_token("old-refresh")

        stored = data_store.load_token(config.shop_id)
        assert stored.access_token == "refreshed_access"

    def test_raises_authentication_error_on_invalid_refresh_token(self, client):
        error_payload = {"error": "access_denied", "message": "Refresh token expired"}
        mock_resp = _mock_response(error_payload, status_code=401)
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401")

        with patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(AuthenticationError):
                client.refresh_token("expired-refresh-token")


# ---------------------------------------------------------------------------
# get_valid_token
# ---------------------------------------------------------------------------

class TestGetValidToken:
    def _store_token(
        self,
        data_store: DataStore,
        shop_id: int,
        access_token: str = "stored_access",
        expires_delta: timedelta = timedelta(hours=4),
    ) -> TokenPair:
        token = TokenPair(
            access_token=access_token,
            refresh_token="stored_refresh",
            expires_at=datetime.now(tz=timezone.utc) + expires_delta,
            shop_id=shop_id,
        )
        data_store.save_token(token)
        return token

    def test_returns_stored_access_token_when_valid(self, client, data_store, config):
        self._store_token(data_store, config.shop_id, access_token="valid_token")
        result = client.get_valid_token()
        assert result == "valid_token"

    def test_raises_when_no_token_in_store(self, client):
        with pytest.raises(AuthenticationError) as exc_info:
            client.get_valid_token()

        # Error must be descriptive.
        assert str(exc_info.value)

    def test_auto_refreshes_when_token_expiring_soon(self, client, data_store, config):
        """Token expiring within the buffer window should be refreshed transparently."""
        # Store a token that expires in 30s (less than _REFRESH_BUFFER_SECONDS).
        self._store_token(
            data_store,
            config.shop_id,
            access_token="expiring_soon",
            expires_delta=timedelta(seconds=_REFRESH_BUFFER_SECONDS - 10),
        )

        new_payload = _make_token_response(access_token="fresh_token")
        with patch.object(client._session, "post", return_value=_mock_response(new_payload)):
            result = client.get_valid_token()

        assert result == "fresh_token"

    def test_does_not_refresh_when_token_has_plenty_of_time(self, client, data_store, config):
        """Token with >60s remaining must NOT trigger a network refresh call."""
        self._store_token(
            data_store,
            config.shop_id,
            access_token="long_lived",
            expires_delta=timedelta(hours=2),
        )

        with patch.object(client._session, "post") as mock_post:
            result = client.get_valid_token()

        mock_post.assert_not_called()
        assert result == "long_lived"

    def test_auto_refresh_saves_new_token(self, client, data_store, config):
        self._store_token(
            data_store,
            config.shop_id,
            access_token="about_to_expire",
            expires_delta=timedelta(seconds=_REFRESH_BUFFER_SECONDS - 5),
        )
        new_payload = _make_token_response(access_token="refreshed")
        with patch.object(client._session, "post", return_value=_mock_response(new_payload)):
            client.get_valid_token()

        stored = data_store.load_token(config.shop_id)
        assert stored.access_token == "refreshed"


# ---------------------------------------------------------------------------
# sign_request (authenticated endpoints)
# ---------------------------------------------------------------------------

class TestSignRequest:
    def _expected_sign(self, partner_id: int, path: str, timestamp: int, access_token: str, shop_id: int, partner_key: str) -> str:
        base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
        return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

    def test_returns_required_keys(self, client):
        with patch("shopee_analyzer.auth.time.time", return_value=1700000000):
            result = client.sign_request("/api/v2/order/get_order_list", "some_access_token")

        assert set(result.keys()) == {"partner_id", "timestamp", "sign", "shop_id", "access_token"}

    def test_sign_matches_expected_hmac(self, client, config):
        fixed_time = 1700000000
        access_token = "test_access_token"
        path = "/api/v2/order/get_order_list"

        with patch("shopee_analyzer.auth.time.time", return_value=fixed_time):
            result = client.sign_request(path, access_token)

        expected = self._expected_sign(
            partner_id=config.partner_id,
            path=path,
            timestamp=fixed_time,
            access_token=access_token,
            shop_id=config.shop_id,
            partner_key=config.partner_key,
        )
        assert result["sign"] == expected

    def test_partner_id_and_shop_id_in_result(self, client, config):
        with patch("shopee_analyzer.auth.time.time", return_value=1700000000):
            result = client.sign_request("/api/v2/product/get_item_list", "tok")

        assert result["partner_id"] == str(config.partner_id)
        assert result["shop_id"] == str(config.shop_id)

    def test_access_token_echoed_in_result(self, client):
        token_val = "my_access_token_value"
        with patch("shopee_analyzer.auth.time.time", return_value=1700000000):
            result = client.sign_request("/api/v2/some/path", token_val)

        assert result["access_token"] == token_val

    def test_timestamp_in_result(self, client):
        fixed_time = 1710000000
        with patch("shopee_analyzer.auth.time.time", return_value=fixed_time):
            result = client.sign_request("/api/v2/path", "tok")

        assert result["timestamp"] == str(fixed_time)

    def test_different_paths_produce_different_signs(self, client):
        access_token = "tok"
        fixed_time = 1700000000
        with patch("shopee_analyzer.auth.time.time", return_value=fixed_time):
            r1 = client.sign_request("/api/v2/order/get_order_list", access_token)
            r2 = client.sign_request("/api/v2/product/get_item_list", access_token)

        assert r1["sign"] != r2["sign"]


# ---------------------------------------------------------------------------
# Auth-endpoint signing (internal — tested via get_access_token call)
# ---------------------------------------------------------------------------

class TestAuthEndpointSigning:
    """Verify the auth-only signing string (no access_token component) is used
    for token-acquisition and token-refresh requests."""

    def _expected_auth_sign(
        self, partner_id: int, path: str, timestamp: int, partner_key: str
    ) -> str:
        base = f"{partner_id}{path}{timestamp}"
        return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

    def test_get_access_token_sign_excludes_access_token(self, client, config):
        payload = _make_token_response()
        fixed_time = 1700000000

        with patch("shopee_analyzer.auth.time.time", return_value=fixed_time):
            with patch.object(
                client._session, "post", return_value=_mock_response(payload)
            ) as mock_post:
                client.get_access_token("code")

        params = mock_post.call_args[1]["params"]
        expected_sign = self._expected_auth_sign(
            config.partner_id, "/api/v2/auth/token/get", fixed_time, config.partner_key
        )
        assert params["sign"] == expected_sign

    def test_refresh_token_sign_excludes_access_token(self, client, config):
        payload = _make_token_response()
        fixed_time = 1700000000

        with patch("shopee_analyzer.auth.time.time", return_value=fixed_time):
            with patch.object(
                client._session, "post", return_value=_mock_response(payload)
            ) as mock_post:
                client.refresh_token("rt")

        params = mock_post.call_args[1]["params"]
        expected_sign = self._expected_auth_sign(
            config.partner_id, "/api/v2/auth/access_token/get", fixed_time, config.partner_key
        )
        assert params["sign"] == expected_sign


# ---------------------------------------------------------------------------
# Retry behaviour — Requirement 1.5: 3 retries, 5-second intervals
# ---------------------------------------------------------------------------

class TestRetryBehaviour:
    def test_retries_exactly_3_times_on_connection_error(self, client):
        """The HTTP call must be attempted exactly 3 times total before giving up."""
        with patch.object(
            client._session,
            "post",
            side_effect=requests.ConnectionError("timeout"),
        ) as mock_post:
            with patch("shopee_analyzer.utils.time.sleep"):  # skip actual sleep
                with pytest.raises((requests.ConnectionError, APIConnectionError)):
                    client.get_access_token("code")

        assert mock_post.call_count == 3

    def test_retries_3_times_on_timeout(self, client):
        with patch.object(
            client._session,
            "post",
            side_effect=requests.Timeout("timed out"),
        ) as mock_post:
            with patch("shopee_analyzer.utils.time.sleep"):
                with pytest.raises((requests.Timeout, APIConnectionError)):
                    client.get_access_token("code")

        assert mock_post.call_count == 3

    def test_succeeds_on_second_attempt(self, client):
        """If the first call fails but the second succeeds, the result is returned."""
        payload = _make_token_response()
        responses = [
            requests.ConnectionError("first failure"),
            _mock_response(payload),
        ]
        with patch.object(client._session, "post", side_effect=responses):
            with patch("shopee_analyzer.utils.time.sleep"):
                token = client.get_access_token("code")

        assert token.access_token == "acc_token_abc"

    def test_no_retry_on_auth_failure_response(self, client):
        """Authentication failures (invalid creds) must NOT be retried."""
        error_payload = {"error": "error_auth", "message": "bad credentials"}
        mock_resp = _mock_response(error_payload, status_code=401)
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401")

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            with pytest.raises(AuthenticationError):
                client.get_access_token("code")

        # Auth failures are not retriable — only 1 call should be made.
        assert mock_post.call_count == 1

    def test_sleep_called_between_retries(self, client):
        """Each retry must be preceded by a 5-second sleep (Req 1.5)."""
        with patch.object(
            client._session,
            "post",
            side_effect=requests.ConnectionError("down"),
        ):
            with patch("shopee_analyzer.utils.time.sleep") as mock_sleep:
                with pytest.raises((requests.ConnectionError, APIConnectionError)):
                    client.get_access_token("code")

        # 3 attempts → 2 sleeps between them.
        assert mock_sleep.call_count == 2
        for sleep_call in mock_sleep.call_args_list:
            assert sleep_call == call(5)


# ---------------------------------------------------------------------------
# partner_key must never appear in logs
# ---------------------------------------------------------------------------

class TestPartnerKeyNotLogged:
    def test_partner_key_absent_from_log_records(self, client, config, caplog):
        """Ensure partner_key does not leak into any log output."""
        import logging
        payload = _make_token_response()
        with caplog.at_level(logging.DEBUG, logger="shopee_analyzer.auth"):
            with patch.object(client._session, "post", return_value=_mock_response(payload)):
                client.get_access_token("code")

        full_log = caplog.text
        assert config.partner_key not in full_log
