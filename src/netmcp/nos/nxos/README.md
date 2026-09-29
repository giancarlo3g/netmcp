# Cisco NX-OS Backend

`NXOSBackend` implements the 4 BGP read methods (`get_bgp_summary`, `get_bgp_neighbors`,
`get_bgp_neighbor`, `get_bgp_config`) over NETCONF, using the native
`Cisco-NX-OS-device` YANG model. Every other method falls through to `NotImplementedBackend`.

Tested against Cisco N9Kv 10.6.3F (`cisco_n9kv`).

**Transport:** NETCONF over SSH via ncclient, port 830 (`feature netconf`), default login admin/admin.

gNMI is not used: NX-OS gNMI (port 50051) is TLS-only, and without a configured
certificate it serves an auto-generated day-1 certificate that expires after 24 hours.
After that, every handshake fails with `certificate has expired`, even with skip_verify.

## Model

Namespace `http://cisco.com/ns/yang/cisco-nx-os-device`, read with subtree filters:

| Data | Path |
|---|---|
| Local AS | `System/bgp-items/inst-items/asn` |
| Default VRF | `inst-items/dom-items/Dom-list[name=default]` (`rtrId`, `numPeers`, `numEstPeers`) |
| Peer | `Dom-list/peer-items/Peer-list[addr=X]` (`peerImp` = inherited template) |
| Peer session | `Peer-list/ent-items/PeerEntry-list` (`operSt`, `operAsn`, `connEst`, `lastFlapTs`) |
| Peer AFI | `PeerEntry-list/af-items/PeerAfEntry-list[type=l2vpn-evpn]` (`tblSt`, `acceptedPaths`, `pfxSent`) |
| Peer templates | `Dom-list/peercont-items/PeerCont-list[name=T]` (`asn`, `srcIf`, AFs) |

- `get_bgp_summary`/`get_bgp_neighbors` produce the same compact view as the other NOSes:
  `operSt` is uppercased (`ESTABLISHED`), only AFIs with `tblSt up` are listed, with names mapped to
  OpenConfig spelling (`l2vpn-evpn` → `L2VPN_EVPN`), and `received` = `acceptedPaths`, `sent` = `pfxSent`.
  There is no per-peer installed counter, and no `total-paths`/`total-prefixes`.
- `peer-as` is `operAsn`; it falls back to the peer's or its template's `asn`.
- `get_bgp_neighbor` returns the raw `Peer-list` entry. A filter for an unknown peer returns only the
  `Dom-list` key skeleton, which is reported as not found.
- `get_bgp_config` is `<get-config source=running>` on `bgp-items`.
- The client decodes replies into dicts: namespaces are stripped, `*-list` elements are always
  lists, and leaves are strings (the backend converts counters to int).
- The instance also has an internal `Dom-list` named `egress-loadbalance-resolution-`, so
  filters always select `name=default`.
