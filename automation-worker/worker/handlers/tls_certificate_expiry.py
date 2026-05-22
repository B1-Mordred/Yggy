# Read-only TLS certificate expiry checks for approved endpoints.
from __future__ import annotations

import math
import socket
import ssl
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

CertFetcher = Callable[[dict[str, Any], int], dict[str, Any]]
CERT_TIME_FORMAT = "%b %d %H:%M:%S %Y %Z"


def fetch_tls_certificate(endpoint: dict[str, Any], timeout: int) -> dict[str, Any]:
    host = str(endpoint["host"])
    port = int(endpoint.get("port") or 443)
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls_sock:
            cert = tls_sock.getpeercert()
    return dict(cert or {})


def parse_certificate_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("certificate notAfter is missing")
    parsed = datetime.strptime(value.strip(), CERT_TIME_FORMAT)
    return parsed.replace(tzinfo=timezone.utc)


def days_until(expires_at: datetime, now: datetime) -> int:
    seconds = (expires_at - now).total_seconds()
    if seconds >= 0:
        return int(math.ceil(seconds / 86400))
    return int(math.floor(seconds / 86400))


def certificate_name(cert: dict[str, Any], key: str) -> str | None:
    sequence = cert.get(key)
    if not isinstance(sequence, tuple):
        return None
    for group in sequence:
        if not isinstance(group, tuple):
            continue
        for item in group:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "commonName":
                return str(item[1])
    return None


def check_tls_endpoint(
    endpoint: dict[str, Any],
    timeout: int,
    *,
    certificate_fetcher: CertFetcher = fetch_tls_certificate,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    warning_threshold = int(endpoint.get("warning_threshold_days") or 30)
    critical_threshold = int(endpoint.get("critical_threshold_days") or 14)
    result: dict[str, Any] = {
        "endpoint_id": str(endpoint.get("endpoint_id") or ""),
        "host": str(endpoint.get("host") or ""),
        "port": int(endpoint.get("port") or 443),
        "warning_threshold_days": warning_threshold,
        "critical_threshold_days": critical_threshold,
        "ok": False,
    }
    try:
        cert = certificate_fetcher(endpoint, timeout)
        expires_at = parse_certificate_time(cert.get("notAfter"))
        remaining_days = days_until(expires_at, current)
        result.update(
            {
                "common_name": certificate_name(cert, "subject") or result["host"],
                "issuer_common_name": certificate_name(cert, "issuer") or "unknown",
                "expires_at": expires_at.isoformat(),
                "days_remaining": remaining_days,
            }
        )
        if remaining_days <= 0:
            result.update(
                {
                    "status": "expired",
                    "severity": "critical",
                    "detail": f"certificate expired {abs(remaining_days)} day(s) ago",
                }
            )
        elif remaining_days <= critical_threshold:
            result.update(
                {
                    "status": "critical",
                    "severity": "critical",
                    "detail": f"certificate expires in {remaining_days} day(s)",
                }
            )
        elif remaining_days <= warning_threshold:
            result.update(
                {
                    "status": "warning",
                    "severity": "warning",
                    "detail": f"certificate expires in {remaining_days} day(s)",
                }
            )
        else:
            result.update(
                {
                    "ok": True,
                    "status": "ok",
                    "severity": "none",
                    "detail": "certificate lifetime is within policy",
                }
            )
    except Exception as exc:
        result.update(
            {
                "status": "handshake_failed",
                "severity": "critical",
                "error": exc.__class__.__name__,
                "detail": str(exc)[:200],
            }
        )
    return result


def render_tls_certificate_message(
    task_config: dict[str, Any],
    endpoints: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    notify: bool,
) -> str:
    title = task_config.get("name", "TLS certificate expiry check")
    dry_run = task_config.get("runtime", {}).get("dry_run", True)
    status = "degraded" if anomalies else "ok"
    lines = [f"**{title}**", "", f"Status: {status}", f"Mode: {'dry-run' if dry_run else 'ready'}", ""]
    if anomalies:
        lines.append("**Anomalies**")
        for item in anomalies:
            target = f"{item.get('host')}:{item.get('port')}"
            detail = item.get("detail") or item.get("error") or item.get("status")
            lines.append(f"- `{item.get('endpoint_id')}` {target}: {item.get('status')} - {detail}")
        lines.extend(
            [
                "",
                "**Suggested action**",
                "Inspect the certificate chain for the approved endpoint before changing proxy, DNS, ACME, firewall, Docker, or service configuration.",
            ]
        )
    else:
        lines.append("No TLS certificate anomalies detected.")
        if not notify:
            lines.append("Discord alert suppressed by anomaly-only output policy.")
    lines.extend(["", "**Endpoints**"])
    for item in endpoints:
        status_label = "ok" if item.get("ok") else item.get("status", "failed")
        lines.append(
            f"- `{item.get('endpoint_id')}`: {status_label}, expires in `{item.get('days_remaining', 'n/a')}` day(s)"
        )
    return "\n".join(lines)


def run_tls_certificate_expiry(
    task_config: dict[str, Any],
    *,
    certificate_fetcher: CertFetcher = fetch_tls_certificate,
    now: datetime | None = None,
) -> dict[str, Any]:
    timeout = int(task_config.get("runtime", {}).get("timeout_seconds") or 60)
    results = [
        check_tls_endpoint(endpoint, timeout, certificate_fetcher=certificate_fetcher, now=now)
        for endpoint in task_config.get("tls_endpoints", [])
    ]
    anomalies = [item for item in results if not item.get("ok")]
    anomaly_only = "anomal" in str(task_config.get("output", {}).get("format", "")).lower()
    notify = bool(anomalies or not anomaly_only)
    return {
        "status": "ok" if not anomalies else "degraded",
        "endpoints": results,
        "ok_count": len(results) - len(anomalies),
        "failed_count": len(anomalies),
        "notify": notify,
        "message": render_tls_certificate_message(task_config, results, anomalies, notify),
    }
