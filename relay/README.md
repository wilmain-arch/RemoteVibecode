# RemoteVibecode relay for Debian 13 and Ubuntu 26.04

This package installs **only the server-side relay**. It does not install Codex,
the PC bridge, or the Android app. One relay instance accepts one PC agent at a
time and multiple simultaneous phone connections. All phone bytes are forwarded
unchanged to the PC bridge; the phone must continue to pin the PC bridge's TLS
certificate. The server's separate TLS certificate protects the PC agent's
control and data connections.

## Build and install

Build on a Linux machine with `bash`, GNU `tar`, `ar`, and `openssl`:

```sh
./relay/build-deb.sh
```

Copy `dist/remotevibecode-relay_0.1.0_all.deb` to the **friend's server** and run:

```sh
sudo apt install ./remotevibecode-relay_0.1.0_all.deb
sudo systemctl status remotevibecode-relay
```

The package creates `/etc/remotevibecode/relay-secret` and a self-signed TLS
certificate/key on first installation, with restrictive permissions. Upgrades
preserve these credentials. Purging the package deletes them.

Allow inbound **TCP 8765, 8766, and 8767** in the server firewall and hosting
provider's firewall. Port 8765 carries the phone's TLS connection to the PC;
8766 and 8767 are the agent's authenticated TLS channels. To choose different
ports, edit `/etc/default/remotevibecode-relay` and restart the service. A
public IP address or DNS name must point to the server. No home-router port
forwarding is needed: the PC agent connects outward to this server.

The PC agent configuration will require the server address, the contents of
`/etc/remotevibecode/relay-secret`, and the relay certificate's SHA-256
fingerprint. Read these locally as root; never place the secret in a URL, QR
code, screenshot, or support log:

```sh
sudo cat /etc/remotevibecode/relay-secret
openssl x509 -in /etc/remotevibecode/relay-cert -noout -fingerprint -sha256
```

The Windows PC agent is distributed separately as `RemoteVibecodeAgent.exe`.
Installing the server package alone does not provide remote access. The
end-to-end route has not yet been verified on a real Windows PC and public server.

## Operations

```sh
sudo systemctl status remotevibecode-relay
sudo journalctl -u remotevibecode-relay -f
sudo systemctl restart remotevibecode-relay
```

The relay does not expose a local management dashboard. Keep its
credentials and administration on the server. Rotate the certificate and
secret together with the agent configuration if either is compromised.
