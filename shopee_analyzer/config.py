"""Configuration Parser for the Shopee Sales Analyzer.

Reads config.yaml / config.json, validates via Pydantic v2, and returns a
typed ``Configuration`` dataclass.  Also serialises ``Configuration`` back to
the original format (round-trip support).

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from shopee_analyzer.exceptions import ConfigurationError
from shopee_analyzer.models import Configuration


# ---------------------------------------------------------------------------
# Internal Pydantic schema — mirrors the Configuration dataclass exactly
# ---------------------------------------------------------------------------

class _ConfigSchema(BaseModel):
    """Pydantic v2 model used for validation only; not exposed publicly."""

    partner_id: int
    partner_key: str
    shop_id: int
    data_store_path: str
    output_dir: str
    refresh_schedule: Literal["hourly", "daily", "weekly"]
    default_period_days: int
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    log_file: str = "analyzer.log"

    @field_validator("partner_key")
    @classmethod
    def partner_key_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("partner_key must not be empty")
        return v

    @field_validator("data_store_path", "output_dir")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("path must not be empty")
        return v

    @field_validator("default_period_days")
    @classmethod
    def period_days_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("default_period_days must be a positive integer")
        return v


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class ConfigurationParser:
    """Reads and writes configuration files in YAML or JSON format.

    Usage::

        parser = ConfigurationParser()
        config = parser.parse(Path("config.yaml"))
        parser.print_config(config, Path("config_copy.yaml"))
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, path: Path) -> Configuration:
        """Parse a YAML or JSON configuration file and return a validated
        ``Configuration`` dataclass.

        Parameters
        ----------
        path:
            Absolute or relative path to ``config.yaml``, ``config.yml``,
            or ``config.json``.

        Returns
        -------
        Configuration
            Fully validated configuration object.

        Raises
        ------
        ConfigurationError
            If the file does not exist, cannot be parsed, or contains
            missing / invalid fields.  The error message names every
            offending field.
        """
        raw = self._load_file(path)
        return self._validate(raw)

    def print_config(self, config: Configuration, path: Path) -> None:
        """Serialise a ``Configuration`` object back to a YAML or JSON file.

        The output format is determined by the file extension:
        * ``.yaml`` / ``.yml`` → YAML
        * anything else (e.g. ``.json``) → JSON

        Parameters
        ----------
        config:
            The configuration object to serialise.
        path:
            Destination file path.  Parent directories must exist.

        Raises
        ------
        ConfigurationError
            If the file cannot be written.
        """
        data = self._to_dict(config)
        suffix = path.suffix.lower()
        try:
            with path.open("w", encoding="utf-8") as fh:
                if suffix in {".yaml", ".yml"}:
                    yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
                else:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")  # POSIX-friendly trailing newline
        except OSError as exc:
            raise ConfigurationError(
                f"Cannot write configuration to '{path}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_file(path: Path) -> dict:
        """Load raw key-value pairs from a YAML or JSON file."""
        if not path.exists():
            raise ConfigurationError(
                f"Configuration file not found: '{path}'"
            )

        suffix = path.suffix.lower()
        try:
            with path.open("r", encoding="utf-8") as fh:
                if suffix in {".yaml", ".yml"}:
                    data = yaml.safe_load(fh)
                elif suffix == ".json":
                    data = json.load(fh)
                else:
                    raise ConfigurationError(
                        f"Unsupported configuration format '{suffix}'. "
                        "Use .yaml, .yml, or .json."
                    )
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"Cannot parse configuration file '{path}': {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigurationError(
                f"Cannot read configuration file '{path}': {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ConfigurationError(
                f"Configuration file '{path}' must contain a YAML/JSON mapping, "
                f"got {type(data).__name__}."
            )
        return data

    @staticmethod
    def _validate(raw: dict) -> Configuration:
        """Run Pydantic validation and convert to the ``Configuration`` dataclass."""
        try:
            schema = _ConfigSchema(**raw)
        except ValidationError as exc:
            # Build a human-readable list of field-level errors.
            field_errors: list[str] = []
            for error in exc.errors():
                # `loc` is a tuple of field path components, e.g. ('partner_id',)
                loc = " -> ".join(str(part) for part in error["loc"]) if error["loc"] else "<root>"
                msg = error["msg"]
                field_errors.append(f"  • {loc}: {msg}")
            detail = "\n".join(field_errors)
            raise ConfigurationError(
                f"Configuration validation failed:\n{detail}"
            ) from exc
        except TypeError as exc:
            # Unexpected keyword arguments → unknown fields supplied
            raise ConfigurationError(
                f"Configuration contains unrecognised fields: {exc}"
            ) from exc

        return Configuration(
            partner_id=schema.partner_id,
            partner_key=schema.partner_key,
            shop_id=schema.shop_id,
            data_store_path=schema.data_store_path,
            output_dir=schema.output_dir,
            refresh_schedule=schema.refresh_schedule,
            default_period_days=schema.default_period_days,
            log_level=schema.log_level,
            log_file=schema.log_file,
        )

    @staticmethod
    def _to_dict(config: Configuration) -> dict:
        """Convert a ``Configuration`` dataclass to a plain dict suitable for
        YAML/JSON serialisation."""
        return {
            "partner_id": config.partner_id,
            "partner_key": config.partner_key,
            "shop_id": config.shop_id,
            "data_store_path": config.data_store_path,
            "output_dir": config.output_dir,
            "refresh_schedule": config.refresh_schedule,
            "default_period_days": config.default_period_days,
            "log_level": config.log_level,
            "log_file": config.log_file,
        }
