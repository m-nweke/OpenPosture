"""The tuning surface of the rules engine, as data rather than as code.

## Why a separate package

Live mode (Epic G) needs the posture rules in TypeScript; the API needs them in Python. Two
implementations that drift are worse than one implementation and no live mode, so the duplication
is contained here: **neither implementation hardcodes a number, both load this file.** Retuning is
a one-line change in one place, and a threshold that moved in Python but not in TypeScript is not
expressible.

## Why the loader is here and not in posture-core

``posture_core`` reads no files, opens no sockets and touches no environment variables. That is
the constraint that lets its suite run in seconds anywhere with no fixtures, and it is enforced by
a test rather than by convention. Parsing JSON is I/O, so it lives in the package that is allowed
to do I/O — one level out, depending on the core rather than the other way round.

## Drift is a test failure, not a convention

``rules.json`` and :class:`~posture_core.Thresholds` describe the same set of numbers twice, which
is exactly the arrangement that rots. ``tests/test_spec.py`` fails if a field exists in one and
not the other, or if the shipped values do not round-trip. Adding a threshold without adding it
here is therefore a red build, not a surprise six weeks later when the TypeScript mirror is found
to be using a stale default.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Final

from posture_core import Thresholds

__all__ = ["RULES_PATH", "load_rules", "load_thresholds", "to_rules_dict"]

RULES_PATH: Final = Path(__file__).with_name("rules.json")


def load_rules(path: Path | None = None) -> dict[str, Any]:
    """The raw document, exactly as both implementations see it."""
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def load_thresholds(path: Path | None = None) -> Thresholds:
    """``rules.json`` parsed into the frozen dataclass the engine takes.

    Unknown keys are rejected rather than ignored. A silently-dropped key is how a deployment ends
    up running with a threshold it believes it configured — the file says 12°, the engine uses 20°,
    and nothing anywhere disagrees.
    """
    document = load_rules(path)
    fields = {field.name for field in dataclasses.fields(Thresholds)}
    unknown = sorted(set(document) - fields - {"$schema", "description"})
    if unknown:
        raise ValueError(
            f"{(path or RULES_PATH).name} contains keys the engine does not know: {unknown}. "
            "Either the file is ahead of the code, or a name is misspelled — both would otherwise "
            "be applied as silence."
        )
    return Thresholds(**{key: value for key, value in document.items() if key in fields})


def to_rules_dict(thresholds: Thresholds) -> dict[str, Any]:
    """The dataclass rendered back into the document shape, for regeneration and drift checks."""
    return dataclasses.asdict(thresholds)
