#!/usr/bin/env bash
set -euo pipefail

RELAY_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname -- "$RELAY_DIR")"
VERSION="${1:-1.0.0}"
OUTPUT_DIR="$PROJECT_DIR/dist"
PACKAGE="$OUTPUT_DIR/remotevibecode-relay_${VERSION}_all.deb"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$OUTPUT_DIR" "$WORK_DIR/control" \
    "$WORK_DIR/data/usr/lib/remotevibecode" \
    "$WORK_DIR/data/usr/lib/systemd/system" \
    "$WORK_DIR/data/etc/default" \
    "$WORK_DIR/data/usr/share/doc/remotevibecode-relay"

install -m 0644 "$PROJECT_DIR/LICENSE" "$WORK_DIR/data/usr/share/doc/remotevibecode-relay/copyright"
install -m 0644 "$RELAY_DIR/server.py" "$WORK_DIR/data/usr/lib/remotevibecode/relay.py"
install -m 0644 "$RELAY_DIR/remotevibecode-relay.service" \
    "$WORK_DIR/data/usr/lib/systemd/system/remotevibecode-relay.service"
install -m 0644 "$RELAY_DIR/remotevibecode-relay.default" \
    "$WORK_DIR/data/etc/default/remotevibecode-relay"
install -m 0644 "$RELAY_DIR/README.md" \
    "$WORK_DIR/data/usr/share/doc/remotevibecode-relay/README.md"

cat > "$WORK_DIR/control/control" <<EOF
Package: remotevibecode-relay
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), openssl, systemd
Maintainer: RemoteVibecode contributors <noreply@github.com>
Description: Private TCP relay for RemoteVibecode
 Forwards the phone's encrypted connection to one owner's PC agent.
 The relay cannot read the phone-to-PC TLS traffic.
EOF
for script in postinst prerm postrm; do
    install -m 0755 "$RELAY_DIR/$script" "$WORK_DIR/control/$script"
done
printf '/etc/default/remotevibecode-relay\n' > "$WORK_DIR/control/conffiles"

printf '2.0\n' > "$WORK_DIR/debian-binary"
tar --sort=name --owner=0 --group=0 --numeric-owner -C "$WORK_DIR/control" -czf "$WORK_DIR/control.tar.gz" .
tar --sort=name --owner=0 --group=0 --numeric-owner -C "$WORK_DIR/data" -czf "$WORK_DIR/data.tar.gz" .
rm -f "$PACKAGE"
ar qc "$PACKAGE" "$WORK_DIR/debian-binary" "$WORK_DIR/control.tar.gz" "$WORK_DIR/data.tar.gz"
echo "$PACKAGE"
