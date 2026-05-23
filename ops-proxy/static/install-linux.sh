#!/usr/bin/env sh
set -eu

CERT_URL="${YGGY_CA_CERT_URL:-https://yggy.b1.germering:8443/pki/ca/local/root.crt}"
DEST_NAME="${YGGY_CA_DEST_NAME:-yggy-caddy-root.crt}"
TMP_CERT="$(mktemp)"

cleanup() {
  rm -f "$TMP_CERT"
}
trap cleanup EXIT

if command -v curl >/dev/null 2>&1; then
  curl -kfsSL "$CERT_URL" -o "$TMP_CERT"
elif command -v wget >/dev/null 2>&1; then
  wget --no-check-certificate -qO "$TMP_CERT" "$CERT_URL"
else
  echo "curl or wget is required." >&2
  exit 1
fi

if command -v openssl >/dev/null 2>&1; then
  openssl x509 -in "$TMP_CERT" -noout >/dev/null
  echo "Downloaded certificate:"
  openssl x509 -in "$TMP_CERT" -noout -subject -issuer -dates -fingerprint -sha256
else
  echo "openssl is recommended so you can inspect the certificate fingerprint." >&2
fi

if [ "$(id -u)" -ne 0 ]; then
  echo
  echo "Re-run as root to install this CA certificate." >&2
  echo "Example:" >&2
  echo "  curl -kfsSL https://yggy.b1.germering:8443/pki/ca/local/install-linux.sh | sudo sh" >&2
  exit 1
fi

if command -v update-ca-certificates >/dev/null 2>&1 && [ -d /usr/local/share/ca-certificates ]; then
  install -m 0644 "$TMP_CERT" "/usr/local/share/ca-certificates/$DEST_NAME"
  update-ca-certificates
  echo "Installed into /usr/local/share/ca-certificates/$DEST_NAME"
elif command -v update-ca-trust >/dev/null 2>&1 && [ -d /etc/pki/ca-trust/source/anchors ]; then
  install -m 0644 "$TMP_CERT" "/etc/pki/ca-trust/source/anchors/$DEST_NAME"
  update-ca-trust
  echo "Installed into /etc/pki/ca-trust/source/anchors/$DEST_NAME"
elif command -v trust >/dev/null 2>&1; then
  trust anchor --store "$TMP_CERT"
  echo "Installed with trust anchor."
else
  echo "Unsupported Linux CA store. Install $TMP_CERT manually as a trusted root CA." >&2
  exit 1
fi

echo "Done. Use https://yggy.b1.germering:8443/ops, not the raw IP address."
