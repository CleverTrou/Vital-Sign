"""Read and write the settings in config.sh / config.ps1.

The start scripts (and with them OBS's Advanced Scene Switcher automation)
read these files, so the control app edits the same files instead of
keeping its own settings. That way the app and OBS always start the
bridge with the same sources.

Both files are executed by their shell when loaded (bash ``source`` and
PowerShell dot-sourcing), so every value written here is validated
against a strict character set first; nothing else ever reaches them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .sources import parse_source

# Source specs are a kind plus a BLE MAC, CoreBluetooth UUID or port:
# letters, digits, ':' and '-' cover all of them, and none of those
# characters mean anything special to bash or PowerShell inside quotes.
_SAFE_SPEC = re.compile(r"^[A-Za-z0-9:-]{1,128}$")

DEFAULT_WS_PORT = 8765

_HEADER = "Written by the Vital Sign app; see {example} for every setting."


@dataclass
class Settings:
    sources: list[str] = field(default_factory=list)
    ws_port: int = DEFAULT_WS_PORT


def config_path(root: Path, windows: bool) -> Path:
    return root / ("config.ps1" if windows else "config.sh")


def validate(spec: str) -> str:
    """Return the spec if it is safe and well-formed, else raise ValueError."""
    if not _SAFE_SPEC.match(spec):
        raise ValueError(f"{spec!r} contains characters a source can't contain")
    parse_source(spec)
    return spec


def _active(line: str) -> str | None:
    stripped = line.strip()
    return None if not stripped or stripped.startswith("#") else stripped


def read_settings(path: Path) -> Settings:
    """Read SOURCES/ADDRESS/WS_PORT (or $Sources/$Address/$WsPort).

    An ADDRESS left over from before SOURCES existed is shown as the
    first source, the same way bridge.py treats it.
    """
    settings = Settings()
    if not path.exists():
        return settings
    windows = path.suffix == ".ps1"
    address = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = _active(line)
        if line is None:
            continue
        if windows:
            if m := re.match(r"^\$Sources\s*=\s*@\((.*)\)", line, re.IGNORECASE):
                settings.sources = re.findall(r"[\"']([^\"']*)[\"']", m.group(1))
            elif m := re.match(r"^\$Address\s*=\s*[\"']([^\"']*)[\"']", line, re.IGNORECASE):
                address = m.group(1)
            elif m := re.match(r"^\$WsPort\s*=\s*(\d+)", line, re.IGNORECASE):
                settings.ws_port = int(m.group(1))
        else:
            if m := re.match(r"^SOURCES=([\"']?)(.*?)\1\s*(#.*)?$", line):
                settings.sources = m.group(2).split()
            elif m := re.match(r"^ADDRESS=([\"']?)(.*?)\1\s*(#.*)?$", line):
                address = m.group(2)
            elif m := re.match(r"^WS_PORT=([\"']?)(\d+)\1", line):
                settings.ws_port = int(m.group(2))
    settings.sources = [s for s in settings.sources if _SAFE_SPEC.match(s)]
    if address and _SAFE_SPEC.match(address) and f"auto:{address}" not in settings.sources:
        settings.sources.insert(0, f"auto:{address}")
    return settings


def write_sources(path: Path, sources: list[str]) -> None:
    """Set the sources, keeping everything else in the file as it was.

    An active ADDRESS line is commented out: read_settings already folded
    it into the source list, and leaving it active would add the device
    a second time.
    """
    sources = [validate(s) for s in sources]
    windows = path.suffix == ".ps1"
    if windows:
        new_line = "$Sources = @(" + ", ".join(f'"{s}"' for s in sources) + ")"
        is_sources = re.compile(r"^\s*\$Sources\s*=", re.IGNORECASE)
        is_address = re.compile(r"^\s*\$Address\s*=", re.IGNORECASE)
    else:
        new_line = f'SOURCES="{" ".join(sources)}"'
        is_sources = re.compile(r"^\s*SOURCES=")
        is_address = re.compile(r"^\s*ADDRESS=")

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        example = "config.ps1.example" if windows else "config.sh.example"
        lines = ["# " + _HEADER.format(example=example)]

    out, replaced = [], False
    for line in lines:
        if is_sources.match(line):
            if not replaced:
                out.append(new_line)
                replaced = True
            continue  # drop any duplicate SOURCES lines
        if is_address.match(line):
            out.append("# " + line.strip() + "  # now part of the sources above")
            continue
        out.append(line)
    if not replaced:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
