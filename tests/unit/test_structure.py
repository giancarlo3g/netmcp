"""Architecture guard: enforces the fixed project layout.

  - MCP tools are registered only in src/netmcp/dispatch.py.
  - Each NOS lives in src/netmcp/nos/<nos>/ with __init__.py (NOS_TYPE, BACKEND),
    backend.py (NotImplementedBackend subclass) and client.py (transport only),
    plus an optional README.md. Placeholders have only __init__.py (+ README.md).
  - Backends only implement NOSBackend Protocol methods; new capabilities go
    through registry.py + dispatch.py, never vendor-specific tools.
  - Devices are reached only through YANG-modeled APIs (gNMI, NETCONF,
    RESTCONF): no CLI libraries, no gNMI `cli:` origin / ASCII encoding, no
    NETCONF RPCs that wrap CLI commands.

If one of these tests fails, fix the code to fit the layout rather than
changing the test. See "Architecture rules" in CLAUDE.md.
"""

import ast
import importlib
import inspect
import os
import re
import tomllib
from pathlib import Path

import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import CLAB_KIND_TO_NOS
from netmcp.registry import NOSBackend, NotImplementedBackend
from netmcp.server import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "netmcp"
NOS_DIR = SRC / "nos"
ALLOWED_NOS_FILES = {"__init__.py", "backend.py", "client.py", "README.md"}
NOS_NAMES = sorted(p.name for p in NOS_DIR.iterdir() if p.is_dir() and p.name != "__pycache__")

PROTOCOL_METHODS = {
    name for name, value in vars(NOSBackend).items()
    if callable(value) and not name.startswith("_")
}

_MCP_IMPORT = re.compile(r"^\s*(from\s+mcp[\s.]|import\s+mcp\b)", re.M)
_TOOL_REGISTRATION = re.compile(r"\.(tool|add_tool)\s*\(")


def _py_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def test_tools_registered_only_in_dispatch():
    offenders = [
        str(p.relative_to(SRC)) for p in _py_files()
        if p.name != "dispatch.py" and _TOOL_REGISTRATION.search(p.read_text())
    ]
    assert not offenders, f"MCP tools may only be registered in dispatch.py; found in {offenders}"


def test_nos_modules_do_not_import_mcp():
    offenders = [
        str(p.relative_to(SRC)) for p in NOS_DIR.rglob("*.py")
        if "__pycache__" not in p.parts and _MCP_IMPORT.search(p.read_text())
    ]
    assert not offenders, f"NOS modules must not import the MCP SDK: {offenders}"


@pytest.mark.parametrize("nos", NOS_NAMES)
def test_nos_modules_do_not_import_other_nos(nos):
    other = re.compile(r"netmcp\.nos\.(\w+)")
    offenders = [
        f"{p.name} -> nos.{m}"
        for p in (NOS_DIR / nos).glob("*.py")
        for m in other.findall(p.read_text())
        if m != nos
    ]
    assert not offenders, f"nos/{nos}/ imports another NOS; move shared code to utils/: {offenders}"


# ---------------------------------------------------------------------------
# NOS directory layout
# ---------------------------------------------------------------------------

def test_nos_package_root_contains_only_nos_directories():
    extra = [
        p.name for p in NOS_DIR.iterdir()
        if p.is_file() and p.name != "__init__.py"
    ]
    assert not extra, f"src/netmcp/nos/ may only contain NOS directories; found files {extra}"


@pytest.mark.parametrize("nos", NOS_NAMES)
def test_nos_directory_layout(nos):
    d = NOS_DIR / nos
    subdirs = [p.name for p in d.iterdir() if p.is_dir() and p.name != "__pycache__"]
    files = {p.name for p in d.iterdir() if p.is_file()}
    assert not subdirs, f"nos/{nos}/ must not contain subdirectories (e.g. contexts/): {subdirs}"
    assert not files - ALLOWED_NOS_FILES, f"nos/{nos}/ has files outside the layout: {sorted(files - ALLOWED_NOS_FILES)}"
    assert "__init__.py" in files, f"nos/{nos}/ is missing __init__.py"
    assert ("backend.py" in files) == ("client.py" in files), (
        f"nos/{nos}/ must have both backend.py and client.py, or neither (placeholder)"
    )


# ---------------------------------------------------------------------------
# NOS package contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nos", NOS_NAMES)
def test_nos_package_exports(nos):
    mod = importlib.import_module(f"netmcp.nos.{nos}")
    assert getattr(mod, "NOS_TYPE", None) == nos, f"nos/{nos}/__init__.py must set NOS_TYPE = {nos!r}"
    backend = getattr(mod, "BACKEND", None)
    assert isinstance(backend, NotImplementedBackend), f"nos/{nos} BACKEND must be a NotImplementedBackend (subclass) instance"
    assert backend.nos_type == nos
    assert isinstance(backend, NOSBackend)

    own_functions = [
        name for name, obj in vars(mod).items()
        if inspect.isfunction(obj) and obj.__module__ == mod.__name__
    ]
    assert not own_functions, (
        f"nos/{nos}/__init__.py must only export NOS_TYPE and BACKEND; found functions {own_functions}"
    )


@pytest.mark.parametrize("nos", NOS_NAMES)
def test_backend_class_location(nos):
    backend = importlib.import_module(f"netmcp.nos.{nos}").BACKEND
    if (NOS_DIR / nos / "backend.py").exists():
        assert type(backend) is not NotImplementedBackend, f"nos/{nos}/backend.py exists but BACKEND is still a placeholder"
        assert type(backend).__module__ == f"netmcp.nos.{nos}.backend", (
            f"nos/{nos} backend class must be defined in nos/{nos}/backend.py"
        )
    else:
        assert type(backend) is NotImplementedBackend, f"nos/{nos} has no backend.py, so BACKEND must be NotImplementedBackend"


@pytest.mark.parametrize("nos", NOS_NAMES)
def test_backend_public_methods_are_protocol_methods(nos):
    cls = type(importlib.import_module(f"netmcp.nos.{nos}").BACKEND)
    extra = set()
    for klass in cls.__mro__:
        if klass is NotImplementedBackend or klass is object:
            break
        extra |= {
            name for name, value in vars(klass).items()
            if callable(value) and not name.startswith("_") and name not in PROTOCOL_METHODS
        }
    assert not extra, (
        f"{cls.__name__} defines public methods outside the NOSBackend Protocol: {sorted(extra)}. "
        "Add them to registry.py + dispatch.py, or make them private helpers."
    )


def test_not_implemented_backend_covers_protocol():
    missing = PROTOCOL_METHODS - set(vars(NotImplementedBackend))
    assert not missing, f"NotImplementedBackend is missing Protocol methods: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_registry_matches_nos_directories():
    assert set(REGISTRY) == set(NOS_NAMES), "REGISTRY in server.py must have exactly one entry per nos/<nos>/ directory"
    for nos in NOS_NAMES:
        assert REGISTRY[nos] is importlib.import_module(f"netmcp.nos.{nos}").BACKEND


def test_containerlab_kinds_map_to_known_nos():
    unknown = set(CLAB_KIND_TO_NOS.values()) - set(NOS_NAMES)
    assert not unknown, f"CLAB_KIND_TO_NOS maps to NOS types without a nos/ directory: {unknown}"


# ---------------------------------------------------------------------------
# Device access: YANG-modeled APIs only (gNMI, NETCONF, RESTCONF)
# ---------------------------------------------------------------------------

# Libraries that drive the device CLI (SSH/telnet screen-scraping or
# command-execution APIs such as eAPI/NX-API).
CLI_LIBRARIES = {
    "netmiko", "paramiko", "scrapli", "napalm", "pyeapi", "jsonrpclib",
    "jnpr", "pexpect", "telnetlib", "asyncssh", "fabric", "nornir_netmiko",
    "nornir_scrapli", "nornir_napalm",
}
# gNMI `cli:` origin paths and NETCONF RPCs that wrap CLI commands.
_CLI_STRING = re.compile(r"^\s*/?cli:|<(command|cli|exec|run-command)[\s>/]", re.I)
# Keyword arguments that select a non-YANG origin or encoding.
_CLI_KWARGS = {"origin": {"cli"}, "encoding": {"ascii"}}


def _code_strings(tree):
    """String constants in the module, excluding docstrings."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_no_cli_library_imports():
    offenders = []
    for p in _py_files():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            else:
                continue
            offenders += [
                f"{p.relative_to(SRC)}:{node.lineno} imports {m}"
                for m in mods if m.split(".")[0] in CLI_LIBRARIES
            ]
    assert not offenders, (
        f"Device access must use gNMI/NETCONF/RESTCONF with YANG models, not the CLI: {offenders}"
    )


def test_no_cli_library_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    deps = list(project["project"].get("dependencies", []))
    for group in project.get("dependency-groups", {}).values():
        deps += [d for d in group if isinstance(d, str)]
    names = {re.match(r"[A-Za-z0-9_.-]+", d).group(0).lower().replace("-", "_") for d in deps}
    banned = {"junos_eznc", *CLI_LIBRARIES}
    assert not names & banned, f"pyproject.toml depends on CLI libraries: {sorted(names & banned)}"


def test_no_cli_origin_encoding_or_rpcs():
    offenders = []
    for p in _py_files():
        tree = ast.parse(p.read_text())
        offenders += [
            f"{p.relative_to(SRC)}:{node.lineno} {node.value!r}"
            for node in _code_strings(tree) if _CLI_STRING.search(node.value)
        ]
        offenders += [
            f"{p.relative_to(SRC)}:{kw.lineno} {kw.arg}={kw.value.value!r}"
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg in _CLI_KWARGS and isinstance(kw.value, ast.Constant)
            and str(kw.value.value).lower() in _CLI_KWARGS[kw.arg]
        ]
        offenders += [
            f"{p.relative_to(SRC)}:{val.lineno} {key.value}={val.value!r}"
            for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for key, val in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value in _CLI_KWARGS
            and isinstance(val, ast.Constant) and str(val.value).lower() in _CLI_KWARGS[key.value]
        ]
    assert not offenders, (
        "Use YANG paths (OpenConfig or native) instead of the CLI origin, ASCII encoding "
        f"or CLI-wrapping RPCs: {offenders}"
    )
