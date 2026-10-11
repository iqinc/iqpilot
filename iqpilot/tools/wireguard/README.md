# WireGuard

Keeps the device reachable on your WireGuard network from Wi-Fi or LTE, behind CGNAT, with the address changing under it. The device dials out to your hub, so nothing has to reach it first.

The kernel has no WireGuard and the root filesystem is read-only, so this runs [wireguard-go](https://git.zx2c4.com/wireguard-go) in userspace from `/data/wg`. `wireguardd` starts it whenever the tool is installed, restarts it after a crash, and re-dials when the handshake goes stale.

## Install

On the device, as the normal user:

```sh
cd /data/iqpilot/iqpilot/tools/wireguard
./build.sh
./install.sh
```

`install.sh` prints the device's public key. The private key is generated on the device and never leaves it.

Fill in `/data/wg/wg.env`:

| key | value |
|-----|-------|
| `WG_ADDRESS` | this device's tunnel address, e.g. `10.0.0.2/32` |
| `WG_PEER_PUBKEY` | the hub's public key |
| `WG_ENDPOINT` | the hub as `host:port`; a hostname is re-resolved on every re-dial |
| `WG_ALLOWED_IPS` | comma-separated subnets reached through the tunnel |
| `WG_KEEPALIVE` | seconds between keepalives, keep it above 0 behind NAT |

Add the device on the hub:

```ini
[Peer]
PublicKey = <device public key>
AllowedIPs = 10.0.0.2/32
```

Then turn on WireGuard in **Settings > Network**. The panel shows the tunnel state, address, last handshake and public key.

## Uninstall

`./uninstall.sh` stops the tunnel and removes the binaries but keeps the keys and `wg.env`. `./uninstall.sh --purge` also deletes `/data/wg` and the device keys.

## How it behaves

- `wireguardd` runs at nice 19 and only while `/data/wg` holds both binaries. It polls every 5 s using sysfs and small file reads, and reads the handshake with `sudo wg show` every 30 s once connected. It writes status to `/dev/shm/iq_wireguard.json` on change and every 10 s, so nothing is written to flash.
- `wireguard-go` runs at nice 10 with `GOMAXPROCS=2` and `GOMEMLIMIT=48MiB`.
- The tunnel keeps running when the manager restarts, and `wireguardd` adopts it. A stale handshake (no handshake for 3 min) re-dials every 2 min. A failing bring-up backs off from 30 s to 5 min.
- Routes are added, never replaced. A subnet already routed on another interface (your Wi-Fi LAN, say) is left alone. Default routes and any subnet containing the hub's address are never routed, so the tunnel cannot swallow its own path to the hub. Skipped routes are listed in the WireGuard details in Settings.
- Only the `wireguard-go` started for this interface is ever stopped.
- The switch is the `IQWireGuardEnabled` param, shared by both UIs and the konn3kt app. Turning it off takes the tunnel down within 5 s.
- The konn3kt app can set the hub through the `IQWireGuardConfig` param, which takes precedence over `wg.env`.
- `build.sh` pins wireguard-go and wireguard-tools. Override `WIREGUARD_GO_REF` or `WIREGUARD_TOOLS_REF` to build another release.
