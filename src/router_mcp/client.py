"""gNMI transport layer for SR OS nodes.

SROS gNMI path conventions:
  Config: nokia-conf:configure/...
  State:  nokia-state:state/...

List keys use unquoted values, e.g.:
  router[router-name=Base]
  service/vpls[service-name=1]
"""

import contextlib
import io
import os

from pygnmi.client import gNMIclient

from router_mcp.config import GNMI_PORT, GNMI_USER, get_password


@contextlib.contextmanager
def _suppress_output():
    """Suppress verbose stdout from pygnmi."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _make_gc(host: str) -> gNMIclient:
    """
    Create a fresh gNMIclient for the given host.

    A new client must be created before every call because pygnmi opens
    and closes the gRPC channel inside `with gc:`, consuming the client object.
    """
    return gNMIclient(
        target=(host, GNMI_PORT),
        username=GNMI_USER,
        password=get_password(),
        insecure=True,
    )


def _get_path(gc: gNMIclient, path: str, encoding: str = "json_ietf"):
    """One-shot gNMI GET. Returns the value(s) or None on failure/not-found.

    Returns a list when the path resolves to multiple list entries, or a single
    dict when only one entry is returned.
    """
    with gc:
        try:
            result = gc.get(path=[path], encoding=encoding)
            if result:
                values = []
                for notif in result.get("notification", []):
                    for update in notif.get("update", []):
                        val = update.get("val")
                        if val is not None:
                            values.append(val)
                if not values:
                    return None
                return values if len(values) > 1 else values[0]
            return None
        except Exception as e:
            raise RuntimeError(f"gNMI GET failed for path {path!r}: {e}") from e


def _set_path(gc: gNMIclient, path: str, value, operation: str = "update", encoding: str = "json_ietf"):
    """gNMI SET (update/replace/delete). Returns the response dict or None on failure."""
    with gc:
        if operation != "delete" and not isinstance(value, dict):
            path_parts = path.rstrip("/").split("/")
            leaf_name = path_parts[-1].split("[")[0]
            value = {leaf_name: value}
            path = "/".join(path_parts[:-1])
            if operation == "replace":
                operation = "update"

        try:
            if operation == "update":
                result = gc.set(update=[(path, value)], encoding=encoding)
            elif operation == "replace":
                result = gc.set(replace=[(path, value)], encoding=encoding)
            elif operation == "delete":
                result = gc.set(delete=[path])
            else:
                return None
            return result if result else None
        except Exception:
            return None


def gnmi_get(hostname: str, path: str) -> dict | None:
    """Perform a gNMI GET against hostname at the given YANG path."""
    with _suppress_output():
        return _get_path(_make_gc(hostname), path)


def gnmi_set(hostname: str, path: str, value, operation: str = "update") -> dict | None:
    """Perform a gNMI SET against hostname at the given YANG path."""
    with _suppress_output():
        return _set_path(_make_gc(hostname), path, value, operation=operation)
