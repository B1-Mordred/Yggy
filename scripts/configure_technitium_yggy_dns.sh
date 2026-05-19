#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${TECHNITIUM_BASE_URL:-http://technitium.b1.germering:5380}"
PASSWORD_FILE="${TECHNITIUM_PASSWORD_FILE:-/home/mordred/technitium/secrets/admin-password.txt}"
ZONE="${TECHNITIUM_ZONE:-b1.germering}"
RECORD="${YGGY_HTTPS_HOST:-yggy.${ZONE}}"
ADDRESS="${YGGY_HTTPS_PUBLISHED_HOST:-192.168.2.2}"
TTL="${TECHNITIUM_RECORD_TTL:-3600}"
APPLY=false

usage() {
  cat <<'EOF'
Create or update the Yggy DNS A record in Technitium DNS.

Default mode is dry-run. Use --apply to write the record.

Options:
  --apply             Write the record through the Technitium API.
  --base-url URL      Technitium API base URL.
  --password-file     File containing the Technitium admin password.
  --zone ZONE         DNS zone, default b1.germering.
  --record FQDN       Record name, default yggy.<zone>.
  --address IP        A record address, default YGGY_HTTPS_PUBLISHED_HOST or 192.168.2.2.
  --ttl SECONDS       DNS TTL, default 3600.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=true
      shift
      ;;
    --base-url)
      BASE_URL="${2:?--base-url requires a URL}"
      shift 2
      ;;
    --password-file)
      PASSWORD_FILE="${2:?--password-file requires a path}"
      shift 2
      ;;
    --zone)
      ZONE="${2:?--zone requires a value}"
      shift 2
      ;;
    --record)
      RECORD="${2:?--record requires a value}"
      shift 2
      ;;
    --address)
      ADDRESS="${2:?--address requires an IP address}"
      shift 2
      ;;
    --ttl)
      TTL="${2:?--ttl requires seconds}"
      shift 2
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

json_field() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

echo "Technitium Yggy DNS record"
echo "  mode: $([[ "${APPLY}" == "true" ]] && echo apply || echo dry-run)"
echo "  base URL: ${BASE_URL}"
echo "  zone: ${ZONE}"
echo "  record: ${RECORD}"
echo "  address: ${ADDRESS}"
echo "  ttl: ${TTL}"
echo

if [[ "${APPLY}" != "true" ]]; then
  echo "+ POST ${BASE_URL}/api/zones/records/add domain=${RECORD} zone=${ZONE} type=A ttl=${TTL} overwrite=true ipAddress=${ADDRESS}"
  exit 0
fi

if [[ ! -r "${PASSWORD_FILE}" ]]; then
  echo "Technitium password file is not readable: ${PASSWORD_FILE}" >&2
  exit 1
fi

ADMIN_PASSWORD="$(tr -d '\r\n' < "${PASSWORD_FILE}")"
login_response="$(curl -sS --fail-with-body -X POST \
  --data-urlencode user=admin \
  --data-urlencode "pass=${ADMIN_PASSWORD}" \
  --data-urlencode includeInfo=true \
  "${BASE_URL}/api/user/login")"

TOKEN="$(printf '%s' "${login_response}" | json_field token)"
if [[ -z "${TOKEN}" ]]; then
  echo "Technitium login failed" >&2
  exit 1
fi

cleanup() {
  curl -sS --fail-with-body -X POST -H "Authorization: Bearer ${TOKEN}" "${BASE_URL}/api/user/logout" >/dev/null || true
}
trap cleanup EXIT

response="$(curl -sS --fail-with-body -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-urlencode "domain=${RECORD}" \
  --data-urlencode "zone=${ZONE}" \
  --data-urlencode type=A \
  --data-urlencode "ttl=${TTL}" \
  --data-urlencode overwrite=true \
  --data-urlencode "ipAddress=${ADDRESS}" \
  "${BASE_URL}/api/zones/records/add")"

status="$(printf '%s' "${response}" | json_field status)"
error="$(printf '%s' "${response}" | json_field errorMessage)"
if [[ "${status}" != "ok" ]]; then
  echo "Failed to set ${RECORD}: ${error}" >&2
  exit 1
fi

echo "Set ${RECORD} -> ${ADDRESS}"
