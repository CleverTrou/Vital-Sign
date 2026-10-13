"""Parse --source values into source specs."""

from __future__ import annotations

from dataclasses import dataclass

from .protocols import HDS_DEFAULT_PORT

AUTO = "auto"
HDS = "hds"
DEMO = "demo"
# Kept in sync with vitalsign.ble.BLE_KINDS; duplicated so parsing (and
# its tests) doesn't need Bleak importable.
BLE_KINDS = ("viatom", "oxyii", "pc60", "plx", "hrs")
ALL_KINDS = (AUTO, *BLE_KINDS, HDS, DEMO)

SOURCE_HELP = """\
Where readings come from. Repeat it to combine sources: they are used in
the order given, and each metric comes from the first source with a live
value, so later sources act as fallbacks. Default: auto.

  auto              the one supported Bluetooth device in range
  auto:ADDRESS      that Bluetooth device, protocol detected automatically
  KIND[:ADDRESS]    force a protocol: viatom (Checkme O2 Max, O2Ring...),
                    oxyii (O2Ring S...), pc60 (PC-60FW fingertip),
                    plx (standard pulse oximeter), hrs (standard heart-rate
                    sensor: straps, watches broadcasting heart rate,
                    phone relay apps)
  hds[:PORT]        receive heart rate from the Health Data Server watch
                    app over your network (default port 3476)
  demo              simulated readings, for setting up the overlay

Example: --source viatom --source hds   (ring first, watch fallback)"""


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    address: str | None = None
    port: int | None = None

    def __str__(self) -> str:
        if self.port is not None:
            return f"{self.kind}:{self.port}"
        return f"{self.kind}:{self.address}" if self.address else self.kind


def parse_source(text: str) -> SourceSpec:
    """Parse one --source value. Raises ValueError with a readable message."""
    kind, sep, rest = text.strip().partition(":")
    kind = kind.lower()
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown source {kind!r}; expected one of: {', '.join(ALL_KINDS)}")
    if not sep:
        return SourceSpec(kind)
    if not rest:
        raise ValueError(f"{text!r}: nothing after the ':'")
    if kind == DEMO:
        raise ValueError("demo takes no address")
    if kind == HDS:
        try:
            port = int(rest)
        except ValueError:
            raise ValueError(f"{text!r}: hds takes a port number, e.g. hds:{HDS_DEFAULT_PORT}") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"{text!r}: port must be 1-65535")
        return SourceSpec(kind, port=port)
    # BLE MAC addresses contain colons, so everything after the first one
    # is the address.
    return SourceSpec(kind, address=rest)


def resolve_specs(sources: list[str] | None, address: str | None) -> list[SourceSpec]:
    """Combine --source and the older --address flag into a spec list."""
    specs = [parse_source(s) for s in sources or []]
    if address:
        # --address predates --source; it means "this Bluetooth device".
        specs.insert(0, SourceSpec(AUTO, address=address))
    if not specs:
        specs = [SourceSpec(AUTO)]
    if sum(s.kind == HDS for s in specs) > 1:
        raise ValueError("only one hds source is supported (it is a single network listener)")
    return specs
