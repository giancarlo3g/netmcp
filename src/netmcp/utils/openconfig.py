"""Helpers for walking OpenConfig replies, shared by backends that speak OpenConfig."""

from netmcp.utils.yang import ns_get, strip_prefix


def entries(data, key: str) -> list:
    """Return the list entries named `key` from a GET reply.

    A device may root the reply at the requested container or one level above it,
    so wrapping containers are descended until `key` is found.
    """
    if data is None:
        return []
    if isinstance(data, list):
        out = []
        for item in data:
            out.extend(entries(item, key) or [item])
        return out
    found = ns_get(data, key)
    if found is not None:
        return found if isinstance(found, list) else [found]
    for v in data.values():
        if isinstance(v, dict):
            nested = entries(v, key)
            if nested:
                return nested
    return []


def leaf(entry: dict, name: str):
    """Read a leaf from an entry, looking at the entry itself then its config/state."""
    val = ns_get(entry, name)
    if val is None:
        val = ns_get(ns_get(entry, "config", {}) or {}, name)
    if val is None:
        val = ns_get(ns_get(entry, "state", {}) or {}, name)
    return val


def active_afi_safis(nbr: dict) -> dict[str, dict]:
    """Map active AFI-SAFI name (e.g. "L2VPN_EVPN") -> {received, sent, installed}."""
    afis = {}
    for afi in entries(ns_get(nbr, "afi-safis") or {}, "afi-safi"):
        state = ns_get(afi, "state") or {}
        if not ns_get(state, "active"):
            continue
        prefixes = ns_get(state, "prefixes") or {}
        afis[strip_prefix(leaf(afi, "afi-safi-name"))] = {
            "received": ns_get(prefixes, "received"),
            "sent": ns_get(prefixes, "sent"),
            "installed": ns_get(prefixes, "installed"),
        }
    return afis


def peer_summary(nbr: dict) -> dict:
    """Compact, vendor-neutral view of one OpenConfig BGP neighbor."""
    state = ns_get(nbr, "state") or {}
    return {
        "peer": leaf(nbr, "neighbor-address"),
        "peer-as": leaf(nbr, "peer-as"),
        "peer-group": leaf(nbr, "peer-group"),
        "state": strip_prefix(ns_get(state, "session-state")),
        "established-transitions": ns_get(state, "established-transitions"),
        "last-established": ns_get(state, "last-established"),
        "afi-safis": active_afi_safis(nbr),
    }
