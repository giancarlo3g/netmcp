"""gNMI transport layer for Arista EOS nodes.

EOS gNMI conventions:
  - Default port 6030; `management api gnmi / transport grpc default` has no TLS,
    so the channel is opened insecure.
  - Paths are OpenConfig or Arista experimental YANG, with no origin prefix, e.g.:
      /network-instances/network-instance[name=default]/vlans
      /interfaces/interface[name=Vxlan1]/arista-vxlan
      /arista/eos/evpn/evpn-instances
  - A single SetRequest is applied atomically, so multi-object changes go
    through gnmi_set_batch() rather than several gnmi_set() calls.
"""

import contextlib
import io

from pygnmi.client import gNMIclient

from netmcp.inventory import NodeInfo, resolve_password


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
    )


def _get_path(gc: gNMIclient, path: str, encoding: str = "json_ietf", datatype: str = "all"):
    """One-shot gNMI GET. Returns the value(s), or None if the path does not exist.

    Returns a list when the reply carries several updates, or a single value
    when only one is returned.
    """
    with gc:
        try:
            result = gc.get(path=[path], encoding=encoding, datatype=datatype)
        except Exception as e:
            if "NOT_FOUND" in str(e) or "not found" in str(e).lower():
                return None
            raise RuntimeError(f"gNMI GET failed for path {path!r}: {e}") from e
    values = []
    for notif in (result or {}).get("notification", []):
        for update in notif.get("update", []):
            val = update.get("val")
            if val is not None:
                values.append(val)
    if not values:
        return None
    return values if len(values) > 1 else values[0]


def gnmi_get(node: NodeInfo, path: str, datatype: str = "all"):
    """Perform a gNMI GET against the node at the given YANG path.

    datatype: "all" (default), "config", "state" or "operational".
    """
    with _suppress_output():
        return _get_path(_make_gc(node), path, datatype=datatype)


def gnmi_set_batch(
    node: NodeInfo,
    updates: list[tuple[str, object]] | None = None,
    deletes: list[str] | None = None,
) -> dict:
    """Send deletes and updates in one atomic SetRequest.

    Per the gNMI spec the target applies deletes before updates, so a leaf-list
    can be cleared and rewritten in the same request.
    Raises RuntimeError with the device's error message on failure.
    """
    with _suppress_output():
        gc = _make_gc(node)
        with gc:
            try:
                return gc.set(
                    update=updates or None,
                    delete=deletes or None,
                    encoding="json_ietf",
                )
            except Exception as e:
                raise RuntimeError(f"gNMI SET failed: {e}") from e
