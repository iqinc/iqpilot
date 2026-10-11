# Prompt for the konn3kt agent: WireGuard in the network menu

Copy everything below the line into the konn3kt mobile-app agent.

---

Add a WireGuard section to the device's **Network** screen in the konn3kt app. The device runs a userspace WireGuard tunnel (`wireguardd`) that keeps it reachable on the owner's own WireGuard network from Wi-Fi or LTE. The app shows the tunnel's state, turns it on and off, and edits the hub it connects to. Every call below works over the cloud, the LAN LocalAPI and BLE, so the screen works with no WAN.

## 1. Only show it when it is installed

WireGuard is opt-in. The binaries are built and installed on the device over SSH (`iqpilot/tools/wireguard/README.md`), not from the app. Call `getWireGuardStatus` when the Network screen opens:

- `installed: false` shows one muted row: "WireGuard: not installed on this device". No toggle, no form.
- `installed: true` shows the full section below.

## 2. Read state: `getWireGuardStatus()`

No arguments. Poll every 5 s while the section is on screen, and stop when it isn't.

```json
{
  "installed": true,
  "enabled": true,
  "state": "connected",
  "summary": "connected • 10.8.0.7 • 14s ago",
  "address": "10.8.0.7/32",
  "endpoint": "hub.example.net:51820",
  "lastHandshake": 1790000000.0,
  "handshakeAgeS": 14.2,
  "publicKey": "kQ3v8Xn0bGf4Y1p7m2Zs9cLwTq5RuVhJ6eDaKiNoPyE=",
  "error": "",
  "configSource": "app",
  "config": {
    "address": "10.8.0.7/32",
    "peerPublicKey": "hubkey...=",
    "endpoint": "hub.example.net:51820",
    "allowedIps": ["10.8.0.0/24"],
    "keepalive": 25
  }
}
```

| field | meaning |
|---|---|
| `state` | one of the states in the table below |
| `summary` | a ready-made one-line status; use it as the row subtitle |
| `address` / `endpoint` | the tunnel address and the hub it dials |
| `lastHandshake` | unix seconds of the last handshake, `null` if none |
| `handshakeAgeS` | seconds since that handshake, `null` if none; show it as "12s ago" / "4m ago" |
| `publicKey` | the device's WireGuard public key; the owner adds it as a peer on the hub |
| `error` | why the tunnel is down, or, while it is up, a note about subnets that were not routed |
| `configSource` | `app` when the hub came from the app, `wg.env` when it came from the file on the device |
| `config` | the hub config currently in force; never contains a private key |

States:

| `state` | show as | colour |
|---|---|---|
| `connected` | Connected | green |
| `connecting` | Connecting… | neutral, with a spinner |
| `stale` | Reconnecting (no handshake for 3 min) | amber |
| `waiting` | Waiting for network | neutral |
| `unconfigured` | Needs a hub, plus `error` (e.g. "missing endpoint") | amber, and open the hub form |
| `off` | Off | grey |
| `error` | Error, plus `error` | red |

## 3. On / off: `IQWireGuardEnabled`

| param | type | default | meaning |
|---|---|---|---|
| `IQWireGuardEnabled` | bool | `true` | master switch; the device UIs use the same param |

Read it with `getBoolParams(["IQWireGuardEnabled"])` (or take `enabled` from `getWireGuardStatus`) and write it with `setBoolParam("IQWireGuardEnabled", bool)`. The tunnel goes down within 5 s of turning it off and starts dialling within 5 s of turning it on. The change is safe while driving and never interrupts IQ.Pilot, so don't gate it on onroad state.

## 4. Hub config: `setWireGuardConfig(config)`

The hub the device dials. Always write it through this RPC, never with `setJsonParam`: the RPC validates on the device with the same code the tunnel uses, and `IQWireGuardConfig` is intentionally not in the JSON allowlist.

```json
{
  "address": "10.8.0.7/32",
  "peerPublicKey": "hub public key, base64",
  "endpoint": "hub.example.net:51820",
  "allowedIps": ["10.8.0.0/24"],
  "keepalive": 25
}
```

| field | required | rules |
|---|---|---|
| `address` | yes | the device's tunnel address with a prefix, e.g. `10.8.0.7/32` |
| `peerPublicKey` | yes | the hub's public key |
| `endpoint` | yes | `host:port`; a hostname is re-resolved on every re-dial |
| `allowedIps` | yes | list of subnets reached through the tunnel |
| `keepalive` | no | seconds, 0–65535, default 25; keep it above 0 behind NAT or CGNAT |

Responses:

- `{"success": 1, "config": {...}}` means it was saved; the tunnel picks it up within 5 s.
- `{"success": 0, "error": "missing hub public key"}` means it was rejected. Show `error` inline under the form and keep what the user typed. Other errors look like `invalid value: ...`, `endpoint must be host:port`, `keepalive must be 0-65535 seconds` and `unknown fields: ...`.
- `setWireGuardConfig(null)` clears the app config, and the device falls back to `/data/wg/wg.env`. Label that action "Use the device's wg.env" and show it only when `configSource == "app"`.

## 5. Screen layout

A **WireGuard** group in Network, below Wi-Fi and cellular:

1. **WireGuard** toggle, bound to `IQWireGuardEnabled`, with `summary` as the subtitle and the state colour on the status dot.
2. **Details** (collapsible): tunnel address, hub, last handshake (from `handshakeAgeS`), and the note from `error` when there is one.
3. **This device's key**: `publicKey` in a monospace field with Copy and Share. Explain in one line that it goes into a `[Peer]` block on the hub with `AllowedIPs = <tunnel address>/32`.
4. **Hub**: an edit form prefilled from `config`, with a Save button that calls `setWireGuardConfig`. Validate lightly in the app (non-empty fields, `host:port` shape) but treat the device's `error` as the truth.
5. When `configSource == "wg.env"`, a caption: "Using the config file on the device. Saving here overrides it."

## 6. What not to build

- No private-key field, no key generation and no key export. The private key never leaves the device, and no RPC returns it.
- No install or uninstall buttons. The binaries are built from source on the device over SSH.
- No full-tunnel option. The device never routes `0.0.0.0/0` or `::/0`, or any subnet that contains the hub's own address. If the user adds one, `error` will say it was not routed; show that note and don't treat it as a failure.
