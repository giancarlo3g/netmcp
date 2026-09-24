"""gNMI transport layer for Juniper Junos (Evolved) nodes.

Junos gNMI conventions:
  - Port 32767 (`system services extension-service request-response grpc clear-text
    port 32767`), no TLS, so the channel is opened insecure. Not 57400: it is in
    the Linux ephemeral range, and a local client (trace-relay) can take it first.
  - Get only accepts type=CONFIG and JSON_IETF/ASCII encoding, and reads the
    `openconfig` origin by default. Native Junos config (junos-conf-* YANG) lives
    under the `juniper` origin, e.g. `juniper:/configuration/protocols/bgp`.
  - Operational state is only served over Subscribe. Mode ONCE sends every leaf
    under the path as a PROTO-encoded update, then a sync_response.
  - Junos rejects the QoS marking pygnmi sends by default, and pygnmi's
    subscribe2() parser cannot decode leaflist values, so subscriptions use the
    raw protobuf stream.
"""

import contextlib
import io
import threading

from pygnmi.client import gNMIclient
from pygnmi.create_gnmi_path import gnmi_path_generator

from netmcp.inventory import NodeInfo, resolve_password

SUBSCRIBE_TIMEOUT = 30  # seconds to wait for the sync_response of a ONCE subscription


@contextlib.contextmanager
def _suppress_output():
    """Suppress verbose stdout from pygnmi."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _make_gc(node: NodeInfo) -> gNMIclient:
    """
    Create a fresh gNMIclient for the given node.

    A new client must be created before every call because pygnmi opens
    and closes the gRPC channel inside `with gc:`, consuming the client object.
    """
    return gNMIclient(
        target=(node.fqdn, node.gnmi_port),
        username=node.username,
        password=resolve_password(node),
        insecure=True,
        no_qos_marking=True,
    )


def gnmi_get_config(node: NodeInfo, paths: list[str]) -> dict[str, object]:
    """Get config for several paths in one request, as {reply path: value}.

    Reply paths drop the origin and leading slash, e.g. "configuration/protocols/bgp".
    A path with no config makes the whole request fail with NOT_FOUND, which is
    reported as an empty dict.
    """
    with _suppress_output():
        gc = _make_gc(node)
        with gc:
            try:
                result = gc.get(path=paths, encoding="json_ietf", datatype="config")
            except Exception as e:
                if "not found" in str(e).lower():
                    return {}
                raise RuntimeError(f"gNMI GET failed for {paths!r}: {e}") from e
    return {
        update["path"]: update["val"]
        for notif in (result or {}).get("notification", [])
        for update in notif.get("update", [])
    }


def _typed_value(val):
    """Decode a gNMI TypedValue into a plain Python value."""
    kind = val.WhichOneof("value")
    if kind == "leaflist_val":
        return [_typed_value(v) for v in val.leaflist_val.element]
    if kind == "decimal_val":
        return val.decimal_val.digits / 10 ** val.decimal_val.precision
    return getattr(val, kind) if kind else None


def _insert(tree: dict, elems, value) -> None:
    """Set a leaf in a nested dict. Keyed elements become list entries carrying their keys."""
    node = tree
    for i, elem in enumerate(elems):
        last = i == len(elems) - 1
        if elem.key:
            keys = dict(elem.key)
            items = node.setdefault(elem.name, [])
            entry = next((e for e in items if all(str(e.get(k)) == v for k, v in keys.items())), None)
            if entry is None:
                entry = dict(keys)
                items.append(entry)
            node = entry
        elif last:
            node[elem.name] = value
        else:
            node = node.setdefault(elem.name, {})


def _descend(tree: dict, elems) -> dict | None:
    """Return the subtree at `elems`, or None if the reply had nothing there."""
    node = tree
    for elem in elems:
        node = node.get(elem.name) if isinstance(node, dict) else None
        if elem.key and isinstance(node, list):
            keys = dict(elem.key)
            node = next((e for e in node if all(str(e.get(k)) == v for k, v in keys.items())), None)
        if node is None:
            return None
    return node


def _updates_to_tree(messages) -> dict:
    """Merge the leaf updates of SubscribeResponse messages into one nested dict."""
    tree: dict = {}
    for msg in messages:
        if not msg.HasField("update"):
            continue
        prefix = list(msg.update.prefix.elem)
        for update in msg.update.update:
            _insert(tree, prefix + list(update.path.elem), _typed_value(update.val))
    return tree


def gnmi_subscribe_once(node: NodeInfo, path: str) -> dict | None:
    """Subscribe ONCE to `path` and return its state as a nested dict.

    The dict is rooted at `path` itself (e.g. the neighbor entry for
    `.../neighbors/neighbor[neighbor-address=X]`). Returns None when the node sends
    nothing for the path.
    """
    messages = []
    request = {
        "subscription": [{"path": path, "mode": "sample", "sample_interval": 10_000_000_000}],
        "mode": "once",
        "encoding": "proto",
    }
    with _suppress_output():
        gc = _make_gc(node)
        with gc:
            try:
                stream = gc.subscribe(subscribe=request)
                timer = threading.Timer(SUBSCRIBE_TIMEOUT, stream.cancel)
                timer.start()
                try:
                    for msg in stream:
                        if msg.HasField("sync_response"):
                            break
                        messages.append(msg)
                finally:
                    timer.cancel()
            except Exception as e:
                raise RuntimeError(f"gNMI SUBSCRIBE failed for path {path!r}: {e}") from e
    return _descend(_updates_to_tree(messages), gnmi_path_generator(path).elem)
