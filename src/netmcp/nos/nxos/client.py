"""NETCONF transport layer for Cisco NX-OS nodes.

NX-OS conventions:
  - NETCONF over SSH on port 830 (`feature netconf`). gNMI (50051) is TLS-only and
    its auto-generated day-1 certificate expires after 24 hours, so NETCONF is used.
  - Reads target the native Cisco-NX-OS-device YANG model
    (namespace http://cisco.com/ns/yang/cisco-nx-os-device) with subtree filters.
  - Replies are decoded into nested dicts: namespaces are stripped, `*-list`
    elements always become lists, leaves are strings.
"""

from xml.etree import ElementTree as ET

from ncclient import manager

from netmcp.inventory import NodeInfo, resolve_password


def _connect(node: NodeInfo):
    return manager.connect(
        host=node.fqdn,
        port=node.netconf_port,
        username=node.username,
        password=resolve_password(node),
        hostkey_verify=False,
        look_for_keys=False,
        allow_agent=False,
        device_params={"name": "nexus"},
        timeout=60,
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_to_dict(elem: ET.Element):
    """Decode an element into a dict (containers), list (`*-list` entries) or str (leaves)."""
    children = list(elem)
    if not children:
        return elem.text or ""
    out: dict = {}
    for child in children:
        name = _local(child.tag)
        value = _xml_to_dict(child)
        if name.endswith("-list"):
            out.setdefault(name, []).append(value)
        elif name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(value)
        else:
            out[name] = value
    return out


def _decode(data_xml: str) -> dict | None:
    """Decode a <data> reply; None when it carries nothing."""
    data = ET.fromstring(data_xml.encode())
    if not list(data):
        return None
    return _xml_to_dict(data)


def netconf_get(node: NodeInfo, filter_xml: str) -> dict | None:
    """NETCONF <get> (config + operational state) with a subtree filter."""
    try:
        with _connect(node) as m:
            reply = m.get(filter=("subtree", filter_xml))
    except Exception as e:
        raise RuntimeError(f"NETCONF get failed on {node.fqdn}: {e}") from e
    return _decode(reply.data_xml)


def netconf_get_config(node: NodeInfo, filter_xml: str) -> dict | None:
    """NETCONF <get-config> of the running datastore with a subtree filter."""
    try:
        with _connect(node) as m:
            reply = m.get_config(source="running", filter=("subtree", filter_xml))
    except Exception as e:
        raise RuntimeError(f"NETCONF get-config failed on {node.fqdn}: {e}") from e
    return _decode(reply.data_xml)
