#!/usr/bin/env bash
set -euo pipefail

APPLY=false
ENABLE_UFW=false
DEFAULT_INCOMING="allow"
LAN_CIDR="${AUTOMATION_DASHBOARD_ALLOWED_CIDR:-192.168.2.0/24}"
PORT="${AUTOMATION_API_LAN_PUBLISHED_PORT:-8088}"

usage() {
  cat <<'EOF'
Configure UFW rules for Yggy LAN dashboard/API access.

Default mode is dry-run. Use --apply to change rules and --enable-ufw to enable UFW.

Options:
  --apply                 Run the ufw commands.
  --enable-ufw            Enable UFW after rules are added.
  --lan-cidr CIDR         Trusted source CIDR allowed to reach the API port.
  --port PORT             Published API port, default 8088.
  --default-allow-incoming
                          Preserve existing inbound services; only add explicit 8088 scope.
  --default-deny-incoming
                          Strict mode. Blocks inbound services unless separately allowed.

Examples:
  scripts/configure_lan_firewall.sh
  scripts/configure_lan_firewall.sh --apply --enable-ufw --lan-cidr 192.168.2.0/24
  scripts/configure_lan_firewall.sh --apply --enable-ufw --lan-cidr 192.168.2.25/32
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=true
      shift
      ;;
    --enable-ufw)
      ENABLE_UFW=true
      shift
      ;;
    --lan-cidr)
      LAN_CIDR="${2:?--lan-cidr requires a CIDR value}"
      shift 2
      ;;
    --port)
      PORT="${2:?--port requires a port value}"
      shift 2
      ;;
    --default-allow-incoming)
      DEFAULT_INCOMING="allow"
      shift
      ;;
    --default-deny-incoming)
      DEFAULT_INCOMING="deny"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Invalid port: ${PORT}" >&2
  exit 2
fi

run() {
  echo "+ $*"
  if [[ "${APPLY}" == "true" ]]; then
    sudo -n "$@"
  fi
}

echo "Yggy LAN firewall configuration"
echo "  mode: $([[ "${APPLY}" == "true" ]] && echo apply || echo dry-run)"
echo "  trusted CIDR: ${LAN_CIDR}"
echo "  API port: ${PORT}"
echo "  default incoming policy: ${DEFAULT_INCOMING}"
echo "  enable UFW: ${ENABLE_UFW}"
echo

run ufw default "${DEFAULT_INCOMING}" incoming
run ufw default allow outgoing
run ufw allow OpenSSH comment "Preserve SSH access"
run ufw allow in proto tcp from "${LAN_CIDR}" to any port "${PORT}" comment "Yggy automation API/dashboard from trusted LAN"
run ufw deny in proto tcp to any port "${PORT}" comment "Deny Yggy automation API/dashboard from untrusted sources"

if [[ "${ENABLE_UFW}" == "true" ]]; then
  run ufw --force enable
else
  echo "+ ufw --force enable"
  echo "  skipped because --enable-ufw was not provided"
fi

if [[ "${APPLY}" == "true" ]]; then
  sudo -n ufw status verbose
fi
