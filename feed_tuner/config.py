from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit
import ipaddress


class ConfigError(ValueError):
    pass


def number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{name} must be a finite number between {low} and {high}")
    return float(value)


def endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise ConfigError("model.base_url must be a URL")
    url = urlsplit(value)
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConfigError("model.base_url must have a host and no credentials, query, or fragment")
    local = url.hostname == "localhost"
    try:
        local |= ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        pass
    if url.scheme != "https" and not (url.scheme == "http" and local):
        raise ConfigError("model.base_url requires HTTPS, except loopback HTTP")
    try:
        url.port
    except ValueError as exc:
        raise ConfigError("model.base_url has an invalid port") from exc
    return value.rstrip("/")


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    name: str
    api_key_env: str | None = None
    timeout_seconds: float = 30


@dataclass(frozen=True)
class Config:
    topics: tuple[str, ...]
    platforms: tuple[str, ...]
    model: ModelConfig
    data_dir: Path
    max_minutes: float = 5
    max_pages: int = 6
    max_post_opens: int = 4
    min_score: float = .7
    pause_seconds: float = 3


def load(path: Path) -> Config:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    allowed = {"topics", "platforms", "model", "data_dir", "max_minutes", "max_pages", "max_post_opens", "min_score", "pause_seconds"}
    if set(data) - allowed:
        raise ConfigError(f"Unknown config fields: {sorted(set(data) - allowed)}")
    topics = data.get("topics")
    if not isinstance(topics, list) or not 1 <= len(topics) <= 50 or any(not isinstance(t, str) or not t.strip() or len(t) > 200 for t in topics):
        raise ConfigError("topics must contain 1–50 nonempty strings of at most 200 characters")
    platforms = data.get("platforms", ["x", "threads"])
    if not isinstance(platforms, list) or not platforms or any(p not in ("x", "threads") for p in platforms):
        raise ConfigError("platforms must contain x and/or threads")
    model = data.get("model", {})
    if not isinstance(model, dict) or set(model) - {"base_url", "name", "api_key_env", "timeout_seconds"}:
        raise ConfigError("Invalid model settings")
    if not isinstance(model.get("name"), str) or not model["name"].strip():
        raise ConfigError("model.name must be nonempty")
    key = model.get("api_key_env")
    if key is not None and (not isinstance(key, str) or not key.isidentifier()):
        raise ConfigError("model.api_key_env must name an environment variable")
    values = {}
    for name, default, low, high in [("max_minutes", 5, .1, 60), ("max_pages", 6, 1, 100), ("max_post_opens", 4, 0, 100), ("min_score", .7, 0, 1), ("pause_seconds", 3, 1, 60)]:
        value = number(data.get(name, default), name, low, high)
        if name in ("max_pages", "max_post_opens"):
            if not value.is_integer():
                raise ConfigError(f"{name} must be an integer")
            value = int(value)
        values[name] = value
    directory = data.get("data_dir", "~/.local/share/feed-tuner")
    if not isinstance(directory, str) or not directory.strip():
        raise ConfigError("data_dir must be a nonempty path")
    return Config(tuple(dict.fromkeys(t.strip() for t in topics)), tuple(dict.fromkeys(platforms)), ModelConfig(endpoint(model.get("base_url", "")), model["name"], key, number(model.get("timeout_seconds", 30), "model.timeout_seconds", 1, 120)), Path(directory).expanduser().resolve(), **values)
