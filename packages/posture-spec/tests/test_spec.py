"""Drift detection between `rules.json` and the dataclass it parses into.

The file and `posture_core.Thresholds` describe the same set of numbers twice, which is precisely
the arrangement that rots quietly. These tests turn every way it can rot into a red build:

* a threshold added to the code but not to the file — the TypeScript mirror would keep using a
  stale default and nothing would say so;
* a key in the file the code does not know — a deployment believes it configured 12° while the
  engine runs 20°, and neither disagrees with the other;
* values that no longer round-trip, meaning the shipped defaults and the documented ones diverged.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from posture_core import DEFAULT_THRESHOLDS, Thresholds
from posture_spec import RULES_PATH, load_rules, load_thresholds, to_rules_dict

# Prose keys, not tunables. Listed explicitly so that adding a third one is a deliberate act
# rather than something that silently widens the exemption.
NON_FIELD_KEYS = {"description", "$schema"}


def test_the_shipped_rules_load() -> None:
    assert isinstance(load_thresholds(), Thresholds)


def test_the_file_and_the_dataclass_defaults_agree() -> None:
    """The values in the repository are the values the engine runs by default.

    If they drifted, every test in posture-core would be exercising one set of thresholds while
    every deployment ran another — and both would pass their own checks.
    """
    assert load_thresholds() == DEFAULT_THRESHOLDS


def test_every_threshold_appears_in_the_file() -> None:
    """A field added to the code but not here is a field the TypeScript mirror will never see."""
    document = set(load_rules()) - NON_FIELD_KEYS
    fields = {field.name for field in dataclasses.fields(Thresholds)}
    assert fields - document == set(), f"missing from rules.json: {sorted(fields - document)}"


def test_the_file_contains_nothing_the_engine_would_ignore() -> None:
    document = set(load_rules()) - NON_FIELD_KEYS
    fields = {field.name for field in dataclasses.fields(Thresholds)}
    assert document - fields == set(), f"unknown keys in rules.json: {sorted(document - fields)}"


def test_an_unknown_key_is_rejected_rather_than_dropped(tmp_path: object) -> None:
    """Silence is the dangerous failure here.

    A misspelled key that is simply ignored means the file says one thing, the engine does another,
    and nothing anywhere reports a disagreement.
    """
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "rules.json"
    document = load_rules()
    document["trunk_slouch_degrees"] = 25.0  # plausible misspelling of trunk_slouch_deg
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="trunk_slouch_degrees"):
        load_thresholds(path)


def test_a_contradictory_file_fails_at_load_rather_than_at_use() -> None:
    """`Thresholds.__post_init__` validates, and loading must not bypass it.

    A file with an inverted band produces a rule that can never fire — no exception, no bad number,
    just a classification that has quietly become unreachable.
    """
    import pathlib
    import tempfile

    document = load_rules()
    document["trunk_upright_deg"] = 40.0
    document["trunk_slouch_deg"] = 20.0
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "rules.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError, match="nothing could fall between them"):
            load_thresholds(path)


def test_the_document_round_trips_through_the_dataclass() -> None:
    assert to_rules_dict(load_thresholds()) == {
        key: value for key, value in load_rules().items() if key not in NON_FIELD_KEYS
    }


def test_the_file_is_formatted_the_way_regeneration_writes_it() -> None:
    """So a hand edit shows up as a formatting diff and gets regenerated properly.

    A file that is sometimes hand-edited and sometimes generated ends up with neither the values
    nor the ordering that anyone expects.
    """
    raw = RULES_PATH.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=2) + "\n"


def test_the_version_travels_with_the_values() -> None:
    """Two reports are only comparable if they were produced by the same rules, so the version
    stamp has to live in the same file as the numbers it describes."""
    assert load_rules()["version"] == DEFAULT_THRESHOLDS.version
