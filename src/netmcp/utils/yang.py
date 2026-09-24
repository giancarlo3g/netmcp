"""Helpers for walking json_ietf gNMI replies."""


def ns_get(d: dict, key: str, default=None):
    """dict.get() that also matches a YANG module-prefixed key.

    json_ietf replies prefix keys that cross a module boundary,
    e.g. "srl_nokia-network-instance:network-instance" or
    "arista-exp-eos-vxlan:arista-vxlan".
    """
    if key in d:
        return d[key]
    for k, v in d.items():
        if k.rsplit(":", 1)[-1] == key:
            return v
    return default


def strip_prefix(value):
    """Drop a YANG module prefix from an identityref/enum value ("mod:VLAN" -> "VLAN")."""
    return value.rsplit(":", 1)[-1] if isinstance(value, str) else value
