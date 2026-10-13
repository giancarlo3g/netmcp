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

Read-only: only GET is exposed.
"""

import httpx

from netmcp.inventory import NodeInfo, resolve_password

NVUE_PORT = 8765
TIMEOUT = 10  # seconds
REVISIONS = ("operational", "applied")


def nvue_get(node: NodeInfo, path: str, rev: str = "operational") -> dict | None:
    """GET https://{fqdn}:8765/nvue_v1/{path}?rev={rev} and return the parsed JSON.

    Returns None when the object does not exist (404). Raises RuntimeError on
    authentication failures, other HTTP errors and connection errors.
    """
    if rev not in REVISIONS:
        raise ValueError(f"rev must be one of {REVISIONS}, got {rev!r}")
    url = f"https://{node.fqdn}:{NVUE_PORT}/nvue_v1/{path.lstrip('/')}"
    try:
        with httpx.Client(
            verify=False,
            timeout=TIMEOUT,
            trust_env=False,
            auth=(node.username, resolve_password(node)),
        ) as client:
            resp = client.get(url, params={"rev": rev})
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
