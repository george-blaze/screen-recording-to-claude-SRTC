#!/bin/bash
# Signs the helpers with a local certificate so macOS keeps the Accessibility / Screen Recording / Microphone
# grants across rebuilds. Run once; re-run after any rebuild. You may get one Keychain prompt: click "Always Allow".
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="Video to Claude"
KC="$HOME/Library/Keychains/login.keychain-db"
if ! security find-identity -v -p codesigning | grep -q "$NAME"; then
  TMP="$(mktemp -d)"
  openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
    -subj "/CN=$NAME" -addext "extendedKeyUsage=codeSigning" -addext "keyUsage=digitalSignature" -addext "basicConstraints=CA:FALSE" >/dev/null 2>&1
  # macOS `security import` only understands the legacy PKCS#12 encryption (OpenSSL 3 defaults to AES/SHA-256 MAC)
  openssl pkcs12 -export -legacy -out "$TMP/vtc.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -passout pass:vtc -name "$NAME" 2>/dev/null \
  || openssl pkcs12 -export -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 -out "$TMP/vtc.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -passout pass:vtc -name "$NAME"
  security import "$TMP/vtc.p12" -k "$KC" -P vtc -T /usr/bin/codesign -T /usr/bin/security >/dev/null
  rm -rf "$TMP"
  echo "✔ created signing certificate \"$NAME\" in your login keychain"
fi
for b in vtc-hotkey paste transcribe sheet; do
  [ -f "$DIR/screencast/$b" ] && f="$DIR/screencast/$b" || f="$DIR/$b"
  [ -f "$f" ] || continue
  codesign --force --sign "$NAME" --identifier "com.videotoclaude.$b" --timestamp=none "$f" 2>&1 | grep -v "replacing existing" || true
  echo "✔ signed $f"
done
echo
echo "Now restart the agent and grant the permissions ONE more time (they stick from now on):"
echo "  launchctl kickstart -k gui/\$(id -u)/com.videotoclaude.hotkey"
echo "  Accessibility: select vtc-hotkey, −, then + and add $DIR/vtc-hotkey"
echo "  Screen & System Audio Recording: same −/+ dance;  Microphone: toggle on when asked"
