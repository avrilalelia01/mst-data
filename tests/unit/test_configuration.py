"""Tests for ConfigurationParser.

Covers:
- Property 1: Configuration round-trip (task 3.2)
- Property 2: Configuration error messages name missing fields (task 3.3)
- Example-based tests for malformed field values

Requirements: 8.3, 8.4, 8.5
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from shopee_analyzer.config import ConfigurationParser
from shopee_analyzer.exceptions import ConfigurationError
from shopee_analyzer.models import Configuration


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "partner_id",
    "partner_key",
    "shop_id",
    "data_store_path",
    "output_dir",
    "refresh_schedule",
    "default_period_days",
]

VALID_CONFIG_DICT: dict = {
    "partner_id": 12345,
    "partner_key": "secret-key-abc",
    "shop_id": 67890,
    "data_store_path": "./data/shopee.db",
    "output_dir": "./output",
    "refresh_schedule": "daily",
    "default_period_days": 30,
}


def _write_yaml(tmp_dir: str, data: dict) -> Path:
    """Write *data* to a temporary YAML file and return its path."""
    path = Path(tmp_dir) / "config.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return path


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def config_strategy(draw: st.DrawFn) -> Configuration:
    """Generate a valid Configuration dataclass.

    String fields are restricted to printable ASCII (letters, digits, basic
    punctuation) to avoid YAML serialisation issues with Unicode control
    characters such as U+0085 (NEL) that PyYAML folds into whitespace.
    """
    # Safe alphabet: printable ASCII letters and digits only
    safe_chars = st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_./")
    safe_text = st.text(
        alphabet=safe_chars,
        min_size=1,
        max_size=64,
    ).filter(lambda s: s.strip())

    return Configuration(
        partner_id=draw(st.integers(min_value=1, max_value=10_000_000)),
        partner_key=draw(safe_text),
        shop_id=draw(st.integers(min_value=1, max_value=10_000_000)),
        data_store_path=draw(safe_text),
        output_dir=draw(safe_text),
        refresh_schedule=draw(st.sampled_from(["hourly", "daily", "weekly"])),
        default_period_days=draw(st.integers(min_value=1, max_value=365)),
        log_level=draw(st.sampled_from(["DEBUG", "INFO", "WARN", "ERROR"])),
        log_file=draw(safe_text),
    )


@st.composite
def missing_fields_strategy(draw: st.DrawFn) -> tuple[dict, list[str]]:
    """Generate a config dict with 1+ required fields removed.

    Returns a tuple of (partial_config_dict, list_of_removed_field_names).
    """
    # Choose a non-empty subset of required fields to remove
    num_to_remove = draw(st.integers(min_value=1, max_value=len(REQUIRED_FIELDS)))
    fields_to_remove = draw(
        st.lists(
            st.sampled_from(REQUIRED_FIELDS),
            min_size=num_to_remove,
            max_size=num_to_remove,
            unique=True,
        )
    )

    partial = dict(VALID_CONFIG_DICT)
    for field in fields_to_remove:
        del partial[field]

    return partial, fields_to_remove


# ---------------------------------------------------------------------------
# Property 1: Configuration round-trip
# (Validates: Requirements 8.4, 8.5)
# ---------------------------------------------------------------------------

@given(config_strategy())
@settings(max_examples=50)
def test_configuration_round_trip(config: Configuration) -> None:
    """Property 1: Configuration round-trip
    parse(print_config(config)) == config for all valid configurations.

    **Validates: Requirements 8.4, 8.5**
    """
    parser = ConfigurationParser()
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = Path(tmp) / "config.yaml"
        parser.print_config(config, yaml_path)
        restored = parser.parse(yaml_path)

    assert restored.partner_id == config.partner_id
    assert restored.partner_key == config.partner_key
    assert restored.shop_id == config.shop_id
    assert restored.data_store_path == config.data_store_path
    assert restored.output_dir == config.output_dir
    assert restored.refresh_schedule == config.refresh_schedule
    assert restored.default_period_days == config.default_period_days
    assert restored.log_level == config.log_level
    assert restored.log_file == config.log_file


# ---------------------------------------------------------------------------
# Property 2: Configuration error messages name missing fields
# (Validates: Requirements 8.3)
# ---------------------------------------------------------------------------

@given(missing_fields_strategy())
@settings(max_examples=100)
def test_error_messages_name_missing_fields(
    config_dict_with_missing_fields: tuple[dict, list[str]],
) -> None:
    """Property 2: Configuration error messages name missing fields.

    **Validates: Requirements 8.3**

    For any subset of required fields removed from a valid config, the raised
    ConfigurationError message must contain every removed field name.
    """
    partial_dict, removed_fields = config_dict_with_missing_fields
    parser = ConfigurationParser()

    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = _write_yaml(tmp, partial_dict)
        with pytest.raises(ConfigurationError) as exc_info:
            parser.parse(yaml_path)

    error_message = str(exc_info.value)
    for field_name in removed_fields:
        assert field_name in error_message, (
            f"Expected field name '{field_name}' in error message, got: {error_message!r}"
        )


# ---------------------------------------------------------------------------
# Example-based tests: malformed field values
# ---------------------------------------------------------------------------

class TestMalformedFieldValues:
    """Example-based tests verifying that malformed values produce error messages
    naming the offending field.  Validates: Requirements 8.3"""

    def _parse_dict(self, data: dict) -> None:
        parser = ConfigurationParser()
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_yaml(tmp, data)
            parser.parse(yaml_path)

    # --- partner_id wrong type ---

    def test_partner_id_wrong_type_names_field(self) -> None:
        """Error message names 'partner_id' when it is given a non-integer value."""
        bad = dict(VALID_CONFIG_DICT, partner_id="not-an-int")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "partner_id" in str(exc_info.value)

    # --- shop_id wrong type ---

    def test_shop_id_wrong_type_names_field(self) -> None:
        """Error message names 'shop_id' when it is given a non-integer value."""
        bad = dict(VALID_CONFIG_DICT, shop_id="not-an-int")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "shop_id" in str(exc_info.value)

    # --- default_period_days wrong type ---

    def test_default_period_days_wrong_type_names_field(self) -> None:
        """Error message names 'default_period_days' when given a string."""
        bad = dict(VALID_CONFIG_DICT, default_period_days="thirty")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "default_period_days" in str(exc_info.value)

    # --- default_period_days non-positive ---

    def test_default_period_days_zero_names_field(self) -> None:
        """Error message names 'default_period_days' when value is 0 (non-positive)."""
        bad = dict(VALID_CONFIG_DICT, default_period_days=0)
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "default_period_days" in str(exc_info.value)

    def test_default_period_days_negative_names_field(self) -> None:
        """Error message names 'default_period_days' when value is negative."""
        bad = dict(VALID_CONFIG_DICT, default_period_days=-5)
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "default_period_days" in str(exc_info.value)

    # --- refresh_schedule invalid value ---

    def test_refresh_schedule_invalid_value_names_field(self) -> None:
        """Error message names 'refresh_schedule' when given an invalid literal."""
        bad = dict(VALID_CONFIG_DICT, refresh_schedule="monthly")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "refresh_schedule" in str(exc_info.value)

    # --- partner_key empty ---

    def test_partner_key_empty_names_field(self) -> None:
        """Error message names 'partner_key' when given an empty string."""
        bad = dict(VALID_CONFIG_DICT, partner_key="")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "partner_key" in str(exc_info.value)

    def test_partner_key_whitespace_names_field(self) -> None:
        """Error message names 'partner_key' when given a whitespace-only string."""
        bad = dict(VALID_CONFIG_DICT, partner_key="   ")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "partner_key" in str(exc_info.value)

    # --- data_store_path empty ---

    def test_data_store_path_empty_names_field(self) -> None:
        """Error message names 'data_store_path' when given an empty string."""
        bad = dict(VALID_CONFIG_DICT, data_store_path="")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "data_store_path" in str(exc_info.value)

    # --- output_dir empty ---

    def test_output_dir_empty_names_field(self) -> None:
        """Error message names 'output_dir' when given an empty string."""
        bad = dict(VALID_CONFIG_DICT, output_dir="")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "output_dir" in str(exc_info.value)

    # --- log_level invalid value ---

    def test_log_level_invalid_value_names_field(self) -> None:
        """Error message names 'log_level' when given an invalid literal."""
        bad = dict(VALID_CONFIG_DICT, log_level="VERBOSE")
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(bad)
        assert "log_level" in str(exc_info.value)

    # --- multiple missing fields in one call ---

    def test_multiple_missing_fields_names_each(self) -> None:
        """Error message names all missing fields when multiple are absent at once."""
        missing = ["partner_id", "shop_id", "refresh_schedule"]
        partial = {k: v for k, v in VALID_CONFIG_DICT.items() if k not in missing}
        with pytest.raises(ConfigurationError) as exc_info:
            self._parse_dict(partial)
        error_message = str(exc_info.value)
        for field_name in missing:
            assert field_name in error_message, (
                f"Expected '{field_name}' in error message: {error_message!r}"
            )
