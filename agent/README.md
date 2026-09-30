# RemoteVibecode Windows agent (preview)

`RemoteVibecodeAgent.exe` runs the Codex bridge on the user's Windows PC and
maintains an outbound connection to **that user's own** Debian/Ubuntu relay.
The PC does not need an inbound router port. The relay never receives the
bridge's TLS private key and forwards phone traffic without decrypting it.

## Requirements

- A running `remotevibecode-relay` on the user's server, with TCP ports 8765,
  8766 and 8767 reachable from outside (unless configured otherwise).
- Codex CLI installed and signed in **on this PC**, available as `codex` in
  `PATH` or specified via `codexExecutable` in `agent.json`.
- The Android RemoteVibecode app.

Download `RemoteVibecodeAgent.exe` from the matching preview release or build
it using the GitHub Actions workflow `Windows agent EXE`. The EXE bundles
Python and all application dependencies; Python is not required on the PC.

On first launch the console asks for the relay's public DNS name or IPv4
address, the relay TLS certificate SHA-256 fingerprint and its secret. Obtain
the latter two **on the owner's server**:

```sh
sudo cat /etc/remotevibecode/relay-secret
openssl x509 -in /etc/remotevibecode/relay-cert -noout -fingerprint -sha256
```

The agent writes `%LOCALAPPDATA%\RemoteVibecode\agent.json`, creates a unique
local bridge TLS certificate, opens a QR image and starts the bridge. Scan the
QR in Android. The QR expires after 30 minutes and is deleted after pairing or
expiry. The server secret is stored only in the Windows user's AppData and must
not be shared. The bridge listens only on `127.0.0.1`.

To change server settings, run `RemoteVibecodeAgent.exe --setup`. To start with
Windows sign-in, run `RemoteVibecodeAgent.exe --install-autostart`; to undo it,
use `--remove-autostart`. The user can inspect or delete their local data in
`%LOCALAPPDATA%\RemoteVibecode`.

This preview is a console application. It supports one PC per relay server and
one paired phone per PC bridge. A graphical setup and device-management screen
is planned separately. The server and Windows executable have not yet been
validated together on a real Windows installation.
