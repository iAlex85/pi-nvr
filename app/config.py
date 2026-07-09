"""
Configuration loading for Pi-NVR.

All tunables live in a YAML file (default: config/default_config.yaml in a
dev checkout, or /etc/pi-nvr/config.yaml once installed). Nothing in the
Python code should hardcode a path, port, or threshold that a user might
reasonably want to change -- add it to the YAML schema instead.
"""
from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("PI_NVR_CONFIG", "config/default_config.yaml")
)


class ConfigError(RuntimeError):
    pass


class Config:
    """
    Thread-safe wrapper around the YAML config dict.

    Supports dotted-path get/set (e.g. cfg.get("motion.sensitivity")) and
    persists changes back to disk atomically so the Settings UI can edit
    live config without restarting the service.
    """

    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            raise ConfigError(f"Config file not found: {self.path}")
        with self._lock, open(self.path, "r", encoding="utf-8") as fh:
            self._data = yaml.safe_load(fh) or {}

    def _walk(self, dotted_key: str, create: bool = False):
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node:
                if not create:
                    raise KeyError(dotted_key)
                node[part] = {}
            node = node[part]
        return node, parts[-1]

    def get(self, dotted_key: str, default: Any = None) -> Any:
        with self._lock:
            try:
                node, leaf = self._walk(dotted_key)
                return copy.deepcopy(node.get(leaf, default))
            except KeyError:
                return default

    def set(self, dotted_key: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            node, leaf = self._walk(dotted_key, create=True)
            node[leaf] = value
            if persist:
                self.save()

    def as_dict(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def save(self) -> None:
        """Atomic write: write to a temp file then rename over the target."""
        with self._lock:
            tmp_path = self.path.with_suffix(".yaml.tmp")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(self._data, fh, sort_keys=False)
            os.replace(tmp_path, self.path)


# Process-wide singleton. Import `get_config()` rather than constructing
# Config() directly so the whole app shares one instance and one file lock.
_config_instance: Config | None = None
_instance_lock = threading.Lock()


def get_config() -> Config:
    global _config_instance
    with _instance_lock:
        if _config_instance is None:
            _config_instance = Config()
        return _config_instance
