"""netmcp command line.

  netmcp                                      run the MCP server (stdio, or HTTP when MCP_PORT is set)
  netmcp inventory --from-clab [LAB] [-o F]   write a netmcp.yml from `containerlab inspect`
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from netmcp.inventory import clab_inspect_to_yml


def _containerlab_inspect() -> dict:
    exe = shutil.which("containerlab") or shutil.which("clab")
    if exe is None:
        sys.exit("netmcp: containerlab not found in PATH (run this on the lab host, not in the container)")
    out = subprocess.run(
        [exe, "inspect", "--all", "--format", "json"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit(f"netmcp: containerlab inspect failed: {out.stderr.strip()}")
    # No running labs prints nothing (or a notice) instead of a JSON object.
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _inventory(args: argparse.Namespace) -> None:
    try:
        doc = clab_inspect_to_yml(_containerlab_inspect(), args.from_clab or None)
    except ValueError as e:
        sys.exit(f"netmcp: {e}")
    text = yaml.safe_dump(doc, sort_keys=False)
    if args.output:
        Path(args.output).write_text(text)
        print(f"netmcp: wrote {len(doc['inventory']['nodes'])} node(s) to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def main() -> None:
    parser = argparse.ArgumentParser(prog="netmcp", description="Multi-vendor MCP server for network routers.")
    sub = parser.add_subparsers(dest="command")
    inv = sub.add_parser("inventory", help="generate a netmcp.yml inventory")
    inv.add_argument(
        "--from-clab", metavar="LAB", nargs="?", const="", required=True,
        help="build it from a running containerlab lab (LAB may be omitted when only one is running)",
    )
    inv.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
    args = parser.parse_args()

    if args.command == "inventory":
        _inventory(args)
        return

    # Imported here: loading the server resolves the inventory and registers the tools.
    from netmcp.server import run
    run()


if __name__ == "__main__":
    main()
