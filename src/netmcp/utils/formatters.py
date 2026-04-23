"""Output formatting helpers."""

import json
from typing import Any


def to_json(data: Any, indent: int = 2) -> str:
    """Serialize data to a JSON string."""
    return json.dumps(data, indent=indent, default=str)


def format_node_results(results: dict[str, Any]) -> str:
    """Format per-node result dict as pretty JSON."""
    return json.dumps(results, indent=2, default=str)


def format_dry_run(node: str, path: str, value: Any) -> str:
    """Format a dry-run preview showing what would be sent."""
    return (
        f"[DRY RUN] Node: {node}\n"
        f"  Path:  {path}\n"
        f"  Value: {json.dumps(value, indent=4, default=str)}"
    )
