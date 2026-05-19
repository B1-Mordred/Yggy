from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_CONFIG_PATH = "configs/metrics/services.yaml"


class ServiceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    name: str
    type: str = "http_health"
    url: str
    expected_status: int | None = Field(default=None, ge=100, le=599)
    timeout_seconds: float = Field(default=3.0, ge=0.2, le=15.0)
    enabled: bool = True
    description: str = ""

    @field_validator("type")
    @classmethod
    def type_must_be_supported(cls, value: str) -> str:
        if value not in {"http_health", "worker_heartbeat", "ollama_tags"}:
            raise ValueError("service check type is not supported")
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("service check URL scheme must be http or https")
        if not parsed.hostname:
            raise ValueError("service check URL must include a hostname")
        return value


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    services: list[ServiceCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_config(self) -> "MetricsConfig":
        if self.version != 1:
            raise ValueError("metrics services config version must be 1")
        ids = [service.id for service in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("metrics service ids must be unique")
        return self


def load_config(path: str | os.PathLike[str] | None = None) -> MetricsConfig:
    config_path = Path(path or os.getenv("YGGY_METRICS_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return MetricsConfig()
    data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return MetricsConfig.model_validate(data)
