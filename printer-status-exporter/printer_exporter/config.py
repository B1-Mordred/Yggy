from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_CONFIG_PATH = "configs/printer-status-exporter/printers.yaml"


class SupplyLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    level_percent: int | float | str | None = None
    status: str | None = Field(default=None, max_length=80)


class PrinterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    name: str = Field(min_length=1, max_length=160)
    type: Literal["static_json", "http_json"] = "static_json"
    enabled: bool = True
    url: str | None = None
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_seconds: float = Field(default=3.0, ge=0.2, le=15.0)
    supplies: list[SupplyLevel] = Field(default_factory=list)
    description: str = ""

    @field_validator("url")
    @classmethod
    def url_must_be_plain_http(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("printer upstream URL scheme must be http or https")
        if not parsed.hostname:
            raise ValueError("printer upstream URL must include a hostname")
        if parsed.username or parsed.password:
            raise ValueError("printer upstream URL must not contain credentials")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "PrinterSource":
        if self.type == "http_json" and not self.url:
            raise ValueError("http_json printer source requires url")
        if self.type == "static_json" and not self.supplies:
            raise ValueError("static_json printer source requires supplies")
        return self


class PrinterExporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    printers: list[PrinterSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_config(self) -> "PrinterExporterConfig":
        if self.version != 1:
            raise ValueError("printer exporter config version must be 1")
        ids = [printer.id for printer in self.printers]
        if len(ids) != len(set(ids)):
            raise ValueError("printer ids must be unique")
        return self


def load_config(path: str | os.PathLike[str] | None = None) -> PrinterExporterConfig:
    config_path = Path(path or os.getenv("YGGY_PRINTER_EXPORTER_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return PrinterExporterConfig()
    data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return PrinterExporterConfig.model_validate(data)
