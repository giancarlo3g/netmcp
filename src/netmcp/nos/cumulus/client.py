"""NVUE REST transport layer for NVIDIA Cumulus Linux nodes.

Cumulus conventions:
  - NVUE is the Cumulus Linux 5.x object model; its REST API is defined by NVIDIA's
    OpenAPI 3 specification and serves the model tree as URLs under /nvue_v1/
    (e.g. `vrf/default/router/bgp/neighbor`). It is not a CLI wrapper: replies are
    schema-defined JSON objects.
  - HTTPS on port 8765 with a self-signed certificate, HTTP basic auth.
  - `rev=applied` reads the running config, `rev=operational` the live state.
  - Errors are decided by status code only: a 401 body is nginx HTML, a 404 body is
    an RFC 7807 problem object ("The requested item does not exist.").
  - Proxy env vars are ignored (trust_env=False): through a corporate proxy the
    request comes back empty.
  - gNMI on Cumulus 5.16 has no Get and its Subscribe sent no data, so it is not used.
  - Writes are NVUE revisions: create one, PATCH the changes into it as a single
    JSON tree (null unsets), apply it, poll until it settles. A revision applies
    all-or-nothing. With `system config auto-save` enabled (the default), a
    successful apply ends in `applied_and_saved`.

Reads use nvue_get; nvue_apply is the only function that writes.
"""

import time
from urllib.parse import quote

import httpx

from netmcp.inventory import NodeInfo, resolve_password

NVUE_PORT = 8765
TIMEOUT = 10  # seconds per HTTP request
REVISIONS = ("operational", "applied")
APPLY_TIMEOUT = 60  # seconds for a revision to settle (an EVPN apply takes ~9 s on Cumulus VX)
POLL_INTERVAL = 1  # seconds
_APPLIED = {"applied", "applied_and_saved"}
_FAILED = {"invalid", "ays_fail", "apply_fail"}


def _client(node: NodeInfo) -> httpx.Client:
    return httpx.Client(
        verify=False,
        timeout=TIMEOUT,
        trust_env=False,
        auth=(node.username, resolve_password(node)),
    )


def _url(node: NodeInfo, path: str) -> str:
    return f"https://{node.fqdn}:{NVUE_PORT}/nvue_v1/{path.lstrip('/')}"


def nvue_get(node: NodeInfo, path: str, rev: str = "operational") -> dict | None:
    """GET https://{fqdn}:8765/nvue_v1/{path}?rev={rev} and return the parsed JSON.

    Returns None when the object does not exist (404). Raises RuntimeError on
    authentication failures, other HTTP errors and connection errors.
    """
    if rev not in REVISIONS:
        raise ValueError(f"rev must be one of {REVISIONS}, got {rev!r}")
    try:
        with _client(node) as client:
            resp = client.get(_url(node, path), params={"rev": rev})
    except httpx.HTTPError as e:
        raise RuntimeError(f"NVUE GET {path!r} failed: {e}") from e
    if resp.status_code == 404:
        return None
    if resp.status_code in (401, 403):
        raise RuntimeError(f"NVUE GET {path!r} failed: authentication failed (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"NVUE GET {path!r} failed: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise RuntimeError(f"NVUE GET {path!r} returned a non-JSON body") from e


def _error_text(resp: httpx.Response) -> str:
    """HTTP status plus the RFC 7807 `detail`, when the body is JSON."""
    if resp.status_code in (401, 403):
        return f"authentication failed (HTTP {resp.status_code})"
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")


def _issues(revision: dict) -> str:
    """Join the messages in a revision's transition.issue."""
    issues = ((revision or {}).get("transition") or {}).get("issue") or {}
    messages = [i.get("message") or i.get("code") or str(i) for i in issues.values() if isinstance(i, dict)]
    return "; ".join(messages) or ((revision or {}).get("transition") or {}).get("progress") or "no details"


def nvue_apply(node: NodeInfo, tree: dict, timeout: float = APPLY_TIMEOUT) -> str:
    """Apply `tree` as one NVUE revision and return its final state.

    Creates a revision, PATCHes `tree` into it at the root (/nvue_v1/?rev=<id>),
    applies it with auto-prompt `ays_yes` and polls until it is applied. On a PATCH
    error, a failed apply (`invalid`, `ays_fail`, `apply_fail`) or a timeout, the
    revision is deleted and RuntimeError is raised with NVUE's error text. Never
    asks NVUE to save; the device's auto-save setting decides persistence.
    """
    try:
        with _client(node) as client:
            resp = client.post(_url(node, "revision"))
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"could not create an NVUE revision: {_error_text(resp)}")
            rev_id = next(iter(resp.json()))
            rev_path = f"revision/{quote(rev_id, safe='')}"
            try:
                resp = client.patch(_url(node, "/"), params={"rev": rev_id}, json=tree)
                if resp.status_code != 200:
                    raise RuntimeError(f"NVUE rejected the change: {_error_text(resp)}")
                resp = client.patch(
                    _url(node, rev_path),
                    json={"state": "apply", "auto-prompt": {"ays": "ays_yes"}},
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"NVUE apply request failed: {_error_text(resp)}")
                deadline = time.monotonic() + timeout
                while True:
                    resp = client.get(_url(node, rev_path))
                    revision = resp.json() if resp.status_code == 200 else {}
                    state = revision.get("state")
                    if state in _APPLIED:
                        return state
                    if state in _FAILED:
                        raise RuntimeError(f"NVUE apply {state}: {_issues(revision)}")
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"NVUE apply did not finish within {timeout:g}s (last state {state!r})")
                    time.sleep(POLL_INTERVAL)
            except BaseException:
                # Leave no pending revision behind; keep the original error if cleanup fails.
                try:
                    client.delete(_url(node, rev_path))
                except httpx.HTTPError:
                    pass
                raise
    except httpx.HTTPError as e:
        raise RuntimeError(f"NVUE request failed: {e}") from e
