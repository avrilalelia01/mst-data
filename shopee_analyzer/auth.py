"""Authentication client for the Shopee Open Platform API v2.

Handles OAuth 2.0 token acquisition and refresh, HMAC-SHA256 request signing,
and transparent token management backed by the Data Store.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from shopee_analyzer.exceptions import APIConnectionError, AuthenticationError
from shopee_analyzer.models import Configuration, TokenPair
from shopee_analyzer.storage import DataStore
from shopee_analyzer.utils import retry_with_backoff

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://partner.test-stable.shopeemobile.com"
_PATH_GET_TOKEN = "/api/v2/auth/token/get"
_PATH_REFRESH_TOKEN = "/api/v2/auth/access_token/get"

# Proactively refresh the token if it expires within this many seconds.
_REFRESH_BUFFER_SECONDS = 60

# HTTP status codes considered as "invalid credentials" (not retriable).
_AUTH_FAILURE_CODES = {
    "error_auth",
    "invalid_partner",
    "invalid_sign",
    "unauthorised_access",
    "access_denied",
    "partner_not_exist",
    "shop_not_exist",
}


# ---------------------------------------------------------------------------
# AuthClient
# ---------------------------------------------------------------------------

class AuthClient:
    """Manages Shopee OAuth 2.0 token lifecycle and request signing.

    All outbound HTTP calls are wrapped with ``@retry_with_backoff`` (3 attempts,
    5-second intervals).  After all retries are exhausted the underlying
    ``requests`` exception is converted to ``APIConnectionError``.  A 4xx
    response with an auth-failure error code raises ``AuthenticationError``
    immediately (no retry — credentials won't self-fix).

    The ``partner_key`` is **never** written to log output.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
    """

    def __init__(self, config: Configuration, data_store: DataStore) -> None:
        """Initialise the client with validated configuration and a data store.

        Args:
            config: Validated ``Configuration`` object (partner_id, partner_key,
                    shop_id, etc.).
            data_store: ``DataStore`` instance used to persist and load token pairs.
        """
        self._config = config
        self._data_store = data_store
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_access_token(self, auth_code: str) -> TokenPair:
        """Exchange an authorisation code for an access/refresh token pair.

        Stores the resulting ``TokenPair`` in the Data Store.

        Args:
            auth_code: The one-time authorisation code returned by the Shopee
                       OAuth consent flow.

        Returns:
            A ``TokenPair`` with ``access_token``, ``refresh_token``,
            ``expires_at``, and ``shop_id`` populated.

        Raises:
            AuthenticationError: If the API returns an auth-failure error code.
            APIConnectionError: If the API is unreachable after 3 retries.
        """
        timestamp = int(time.time())
        sign = self._sign_auth_request(_PATH_GET_TOKEN, timestamp)

        params = {
            "partner_id": self._config.partner_id,
            "timestamp": timestamp,
            "sign": sign,
        }
        body = {
            "code": auth_code,
            "shop_id": self._config.shop_id,
            "partner_id": self._config.partner_id,
        }

        logger.info(
            "Requesting access token for shop_id=%s via %s",
            self._config.shop_id,
            _PATH_GET_TOKEN,
        )

        response_data = self._post_with_retry(_BASE_URL + _PATH_GET_TOKEN, params, body)
        token = self._parse_token_response(response_data)
        self._data_store.save_token(token)
        logger.info(
            "Access token obtained and stored for shop_id=%s", self._config.shop_id
        )
        return token

    def refresh_token(self, refresh_token: str) -> TokenPair:
        """Use a refresh token to obtain a new access/refresh token pair.

        Stores the resulting ``TokenPair`` in the Data Store, overwriting the
        previous entry for this shop.

        Args:
            refresh_token: The refresh token string from a previous ``TokenPair``.

        Returns:
            A new ``TokenPair``.

        Raises:
            AuthenticationError: If the API returns an auth-failure error code.
            APIConnectionError: If the API is unreachable after 3 retries.
        """
        timestamp = int(time.time())
        sign = self._sign_auth_request(_PATH_REFRESH_TOKEN, timestamp)

        params = {
            "partner_id": self._config.partner_id,
            "timestamp": timestamp,
            "sign": sign,
        }
        body = {
            "refresh_token": refresh_token,
            "shop_id": self._config.shop_id,
            "partner_id": self._config.partner_id,
        }

        logger.info(
            "Refreshing access token for shop_id=%s via %s",
            self._config.shop_id,
            _PATH_REFRESH_TOKEN,
        )

        response_data = self._post_with_retry(_BASE_URL + _PATH_REFRESH_TOKEN, params, body)
        token = self._parse_token_response(response_data)
        self._data_store.save_token(token)
        logger.info(
            "Access token refreshed and stored for shop_id=%s", self._config.shop_id
        )
        return token

    def get_valid_token(self) -> str:
        """Return a valid access token, auto-refreshing if expiry is imminent.

        Checks the Data Store for an existing ``TokenPair`` for the configured
        ``shop_id``.  If the token expires within the next
        ``_REFRESH_BUFFER_SECONDS`` seconds, transparently refreshes it before
        returning the access token string.

        Returns:
            The current (or freshly refreshed) access token string.

        Raises:
            AuthenticationError: If no token exists or refresh fails due to
                                  invalid credentials.
            APIConnectionError: If the API is unreachable during a refresh
                                 attempt after 3 retries.
        """
        token = self._data_store.load_token(self._config.shop_id)

        if token is None:
            raise AuthenticationError(
                f"No access token found for shop_id={self._config.shop_id}. "
                "Please run the OAuth authorisation flow first (get_access_token)."
            )

        now = datetime.now(tz=timezone.utc)
        # Ensure expires_at is timezone-aware for comparison.
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at - now <= timedelta(seconds=_REFRESH_BUFFER_SECONDS):
            logger.info(
                "Token for shop_id=%s expires soon (at %s); refreshing proactively.",
                self._config.shop_id,
                expires_at.isoformat(),
            )
            token = self.refresh_token(token.refresh_token)

        return token.access_token

    def sign_request(self, path: str, access_token: str) -> dict[str, str]:
        """Build the query-parameter dict needed to authenticate an API request.

        The HMAC-SHA256 signing string for authenticated (non-auth) endpoints is:
        ``"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"``

        Args:
            path: The API path, e.g. ``"/api/v2/order/get_order_list"``.
            access_token: A valid access token string (from ``get_valid_token``).

        Returns:
            A dict with keys ``partner_id``, ``timestamp``, ``sign``,
            ``shop_id``, and ``access_token`` that should be merged into the
            request's query parameters.
        """
        timestamp = int(time.time())
        partner_id = self._config.partner_id
        shop_id = self._config.shop_id

        base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
        sign = self._hmac_sign(base_string)

        return {
            "partner_id": str(partner_id),
            "timestamp": str(timestamp),
            "sign": sign,
            "shop_id": str(shop_id),
            "access_token": access_token,
        }

    # ------------------------------------------------------------------
    # Private helpers — signing
    # ------------------------------------------------------------------

    def _sign_auth_request(self, path: str, timestamp: int) -> str:
        """Compute the HMAC-SHA256 signature for auth-only endpoints.

        Auth endpoints use a shorter signing string (no access_token or shop_id):
        ``"{partner_id}{api_path}{timestamp}"``

        The partner_key is intentionally not logged.
        """
        base_string = f"{self._config.partner_id}{path}{timestamp}"
        return self._hmac_sign(base_string)

    def _hmac_sign(self, base_string: str) -> str:
        """Return the lowercase hex HMAC-SHA256 digest of *base_string*.

        Uses ``self._config.partner_key`` as the secret.  The key is
        **never** written to any log output.
        """
        key = self._config.partner_key.encode("utf-8")
        message = base_string.encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Private helpers — HTTP
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_attempts=3,
        delay_seconds=5,
        exceptions=(requests.ConnectionError, requests.Timeout, requests.RequestException),
    )
    def _post_with_retry(self, url: str, params: dict, body: dict) -> dict:
        """POST *body* as JSON to *url* with *params* as query parameters.

        Decorated with ``@retry_with_backoff`` for transient connection errors.
        Auth-failure responses (4xx with a known error code) are raised as
        ``AuthenticationError`` immediately — these are not retriable.

        Args:
            url: Full endpoint URL.
            params: Query parameters dict (partner_id, timestamp, sign, …).
            body: JSON request body dict.

        Returns:
            Parsed JSON response dict from the Shopee API.

        Raises:
            AuthenticationError: On HTTP 4xx with an auth-failure error code.
            APIConnectionError: After all retries are exhausted (raised by the
                                 retry decorator → re-raised here as
                                 ``APIConnectionError``).
        """
        try:
            response = self._session.post(url, params=params, json=body, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as exc:
            # Re-raise so the retry decorator can intercept it.
            raise exc
        except requests.RequestException as exc:
            raise APIConnectionError(
                f"Unexpected request error contacting Shopee API at {url}: {exc}"
            ) from exc

        # Check for auth-failure before raising for status so we can provide
        # a descriptive message instead of a generic HTTPError.
        if response.status_code in (400, 401, 403):
            self._raise_if_auth_failure(response, url)

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise APIConnectionError(
                f"Shopee API returned HTTP {response.status_code} for {url}: {exc}"
            ) from exc

        try:
            return response.json()
        except ValueError as exc:
            raise APIConnectionError(
                f"Shopee API returned non-JSON response from {url}: {exc}"
            ) from exc

    def _raise_if_auth_failure(self, response: requests.Response, url: str) -> None:
        """Inspect a 4xx response and raise ``AuthenticationError`` if the body
        contains a recognised auth-failure error code.

        If the error code is not one of the known auth-failure codes, the method
        returns without raising so the caller can handle it differently.

        Args:
            response: The requests ``Response`` object.
            url: The request URL (for error messages).

        Raises:
            AuthenticationError: When the response body contains an auth-failure
                                  error code, with a descriptive message.
        """
        try:
            data = response.json()
        except ValueError:
            # Body is not JSON — not an auth-failure we recognise.
            return

        error_code = str(data.get("error", "")).lower()
        message = data.get("message", "")

        if error_code in _AUTH_FAILURE_CODES:
            raise AuthenticationError(
                f"Authentication failed (HTTP {response.status_code}) — "
                f"error code: '{error_code}', message: '{message}'. "
                "Please verify your Partner ID, Partner Key, and Shop ID in "
                "the configuration file."
            )

    # ------------------------------------------------------------------
    # Private helpers — response parsing
    # ------------------------------------------------------------------

    def _parse_token_response(self, data: dict) -> TokenPair:
        """Convert a Shopee token API response dict to a ``TokenPair``.

        Args:
            data: Parsed JSON response from ``/api/v2/auth/token/get`` or
                  ``/api/v2/auth/access_token/get``.

        Returns:
            A ``TokenPair`` with ``expires_at`` set to
            ``now() + expire_in`` seconds.

        Raises:
            AuthenticationError: If the response body contains an error code,
                                   or if required fields are missing.
        """
        # The Shopee API may embed error information even in 200 responses.
        error_code = str(data.get("error", "")).lower()
        if error_code and error_code != "":
            # Some SDKs return "" or None on success, non-empty on error.
            message = data.get("message", "No additional detail provided.")
            if error_code in _AUTH_FAILURE_CODES:
                raise AuthenticationError(
                    f"Token endpoint returned auth failure — "
                    f"error code: '{error_code}', message: '{message}'. "
                    "Please verify your Partner ID, Partner Key, and Shop ID."
                )
            # Non-auth errors: surface as AuthenticationError with context.
            raise AuthenticationError(
                f"Token endpoint returned error '{error_code}': {message}"
            )

        try:
            access_token: str = data["access_token"]
            refresh_token: str = data["refresh_token"]
            expire_in: int = int(data["expire_in"])
            # The API may return shop_id in the token response; fall back to
            # the configured shop_id if absent (sandbox sometimes omits it).
            shop_id: int = int(data.get("shop_id", self._config.shop_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError(
                f"Token response is missing required fields: {exc}. "
                f"Raw response keys: {list(data.keys())}"
            ) from exc

        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expire_in)

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            shop_id=shop_id,
        )
