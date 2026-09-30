# RemoteVibecode: Windows client and server relay

This preview contains two components built from the same source revision:

- `RemoteVibecodeAgent.exe` — graphical PC client for Windows x64. Install and sign in to Codex CLI on that PC first. The client connects outward to your own relay.
- `remotevibecode-relay_0.1.0_all.deb` — server relay for Debian 13 and Ubuntu 26.04. Install it on a server you control.

Install the `.deb` with `sudo apt install ./remotevibecode-relay_0.1.0_all.deb`. Open TCP ports 8765, 8766, and 8767 to the server. Read the relay secret and certificate fingerprint on the server as described in [relay/README.md](https://github.com/wilmain-arch/RemoteVibecode/blob/main/relay/README.md), then enter those values and the server address in the Windows client. Scan the client's QR code with the Android RemoteVibecode app.

`SHA256SUMS.txt` contains checksums for both downloads. This is an unsigned preview. The Android source is in the repository, but an Android installation package is not included in this release. The complete client–relay–phone path has not yet been validated on a real Windows PC and public server.
