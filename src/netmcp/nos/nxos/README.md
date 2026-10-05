# Cisco NX-OS Backend

`NXOSBackend` implements the 4 BGP read methods (`get_bgp_summary`, `get_bgp_neighbors`,
`get_bgp_neighbor`, `get_bgp_config`) and the 5 EVPN methods (VLAN-based EVPN-VXLAN) over
NETCONF, using the native `Cisco-NX-OS-device` YANG model. Every other method falls through
to `NotImplementedBackend`.

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

## EVPN (VLAN-based)

One instance = one VLAN mapped to a VNI. It is spread over four objects, joined on the VNI:

| Object | Path | Leaves |
|---|---|---|
| VLAN + `vn-segment` | `System/bd-items/bd-items/BD-list[fabEncap=vlan-N]` | `name`, `accEncap` = `vxlan-VNI` |
| NVE member VNI | `System/eps-items/epId-items/Ep-list[epId=1]/nws-items/vni-items/Nw-list[vni=VNI]` | `IngRepl-items/proto` = `bgp` |
| `evpn / vni N l2` | `System/evpn-items/bdevi-items/BDEvi-list[encap=vxlan-VNI]` | `rd`, `rttp-items/RttP-list[type=import\|export]/ent-items/RttEntry-list[rtt]` |
| Access trunk | `System/intf-items/phys-items/PhysIf-list[id=ethX/Y]` | `layer`, `mode`, `trunkVlans` (`"10,20-30"`, default `"1-4094"`) |

- RD/RT values are typed strings: `rd:as2-nn2:10:6`, `route-target:as2-nn2:65000:10`
  (`as2-nn4`, `as4-nn2`, `ipv4-nn2` by number size; `rd:unknown:0:0` = `rd auto`). The backend
  reports and accepts `10:6` / `65000:10` (and `target:65000:10`, RD `auto`).
- Lookup is by VLAN name or VLAN id; the VLAN id is reported as the EVI, and `evi`/`service_id` are ignored.
  Only VLANs with a `vn-segment` count as EVPN instances.
- State (`<get>`): `BD-list` `operSt`/`member-items`; `Ep-list` `operState`, `primaryIp`,
  `nws-items/opervni-items/OperNw-list[vni]` (`state`, `type`, `mode`), VTEPs in
  `peers-items/dy_peer-items/DyPeer-list` (all NVE peers, not per VNI), and the MAC table
  `System/mac-items/table-items/vlan-items/MacAddressEntry-list` (`port` = `Nve`, `macInfo` = `nve`
  for remote MACs). The MAC list needs both keys (`vlan`, `macAddress`) in a filter, so the whole
  table is read and filtered by VLAN in the backend.
- Writes are one `<edit-config>` on `running` (merge, `rollback-on-error`), so a rejected payload
  applies nothing. Deletes use `nc:operation="delete"` inline.
- Provision requires `interface_name` (`Ethernet1/1` or `eth1/1`, must be a Layer2 trunk) and
  `vlan_id` (2-3967; 3968+ is reserved). `nve1` must already exist (it is never created). It creates
  the VLAN with `vn-segment`, `member vni` with `ingress-replication protocol bgp`, and `evpn vni N l2`
  with RD and one import and export RT, and adds the VLAN to the trunk allowed list if it isn't carried yet.
- Delete removes the evpn VNI, NVE member VNI and VLAN, and drops the VLAN from trunk allowed lists,
  except the default `1-4094` and lists where it is the only VLAN (left as is and reported).
