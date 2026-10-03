"""Custom exception hierarchy for the Shopee Sales Analyzer.

All application-specific errors subclass ShopeeAnalyzerError so callers can
catch the entire family with a single except clause when needed.
"""


class ShopeeAnalyzerError(Exception):
    """Base class for all Shopee Sales Analyzer exceptions."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------

class ConfigurationError(ShopeeAnalyzerError):
    """Raised when the configuration file is missing, malformed, or invalid.

    Requirements: 8.3
    """


# ---------------------------------------------------------------------------
# Authentication errors
# ---------------------------------------------------------------------------

class AuthenticationError(ShopeeAnalyzerError):
    """Raised when OAuth 2.0 authentication fails due to invalid credentials.

    Requirements: 1.4
    """


class TokenRefreshError(AuthenticationError):
    """Raised when an access-token refresh attempt fails.

    Requirements: 1.3
    """


# ---------------------------------------------------------------------------
# API / network errors
# ---------------------------------------------------------------------------

class APIConnectionError(ShopeeAnalyzerError):
    """Raised after exhausting all retry attempts when the API is unreachable.

    Requirements: 1.5
    """


class APIResponseError(ShopeeAnalyzerError):
    """Raised when the Shopee API returns an unexpected or error response."""


# ---------------------------------------------------------------------------
# Data store errors
# ---------------------------------------------------------------------------

class DataStoreError(ShopeeAnalyzerError):
    """Raised when a Data Store operation fails after all retries.

    Requirements: 9.4
    """


class DataStoreWriteError(DataStoreError):
    """Raised specifically when a write operation fails after all retries.

    Requirements: 9.4
    """


# ---------------------------------------------------------------------------
# Scheduler errors
# ---------------------------------------------------------------------------

class SchedulerError(ShopeeAnalyzerError):
    """Raised when the RefreshScheduler encounters an invalid configuration.

    Requirements: 7.1
    """


# ---------------------------------------------------------------------------
# Collection errors
# ---------------------------------------------------------------------------

class CollectionError(ShopeeAnalyzerError):
    """Raised when the Data Collector encounters a non-recoverable failure.

    Requirements: 2.5
    """


# ---------------------------------------------------------------------------
# Report errors
# ---------------------------------------------------------------------------

class ReportGenerationError(ShopeeAnalyzerError):
    """Raised when the Report Generator cannot produce output files.

    Requirements: 5.1, 5.2, 5.3
    """
