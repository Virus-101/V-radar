"""
Configuration loader for the Mosquito Laser Zapper.

Loads settings from a YAML configuration file and provides
a clean API for accessing configuration values with defaults.
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


class Config:
    """Hierarchical configuration with dot-notation access.

    Usage:
        config = Config("config.yaml")
        config.get("detection.min_area", default=5)
        config.detection.min_area  # attribute access also works
    """

    def __init__(self, path: Optional[str] = None):
        self._path = str(path or DEFAULT_CONFIG_PATH)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load configuration from YAML file."""
        if not os.path.exists(self._path):
            raise FileNotFoundError(
                f"Configuration file not found: {self._path}"
            )
        with open(self._path, "r") as f:
            self._data = yaml.safe_load(f) or {}
        self._validate()

    def _validate(self) -> None:
        """Validate required sections exist."""
        required = [
            "system", "detection", "tracking",
            "pan_tilt", "laser", "web_dashboard", "logging"
        ]
        for section in required:
            if section not in self._data:
                raise ValueError(
                    f"Missing required config section: {section}"
                )

    def reload(self) -> None:
        """Reload configuration from disk."""
        self._load()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot-separated key.

        Args:
            key: Dot-separated path like "detection.min_area"
            default: Value to return if key not found

        Returns:
            The configuration value, or default if not found.
        """
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def section(self, name: str) -> "Config":
        """Get a sub-section as a new Config-like object.

        Args:
            name: Section name (e.g., "detection")

        Returns:
            A new Config view of the section.
        """
        data = self._data.get(name, {})
        # Create a minimal config object that reads from data
        cfg = Config.__new__(Config)
        cfg._path = self._path
        cfg._data = data
        return cfg

    def __getattr__(self, name: str) -> Any:
        """Allow attribute-style access: config.section.key"""
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            return self._name_to_config(name, self._data[name])
        raise AttributeError(f"Config has no key: {name}")

    def _name_to_config(self, name: str, value: Any) -> Any:
        """Wrap dict values in Config for nested access."""
        if isinstance(value, dict):
            cfg = Config.__new__(Config)
            cfg._path = self._path
            cfg._data = value
            return cfg
        return value

    def as_dict(self) -> Dict[str, Any]:
        """Return the full configuration as a dict."""
        return dict(self._data)

    def __repr__(self) -> str:
        return f"Config({self._path})"
