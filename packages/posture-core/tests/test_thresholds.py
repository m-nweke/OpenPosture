"""Tests for the threshold container.

Mostly about the validation, because the values themselves are meant to change and pinning them
here would just make retuning a two-file edit. What must not change is that a *contradictory* set
of values fails loudly at construction rather than producing a rule that silently never fires.
"""

from __future__ import annotations

import dataclasses

import pytest

from posture_core.thresholds import DEFAULT_THRESHOLDS, RULES_VERSION, Thresholds


def test_defaults_construct() -> None:
    assert Thresholds().version == RULES_VERSION


def test_thresholds_are_immutable() -> None:
    """So the shared DEFAULT_THRESHOLDS instance cannot become a mutable global by accident.

    A test that nudged one value on the shared object would leak into every test after it, and the
    failure would surface somewhere unrelated.
    """
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_THRESHOLDS.trunk_slouch_deg = 25.0  # type: ignore[misc]


def test_a_variant_is_made_by_replacement_not_mutation() -> None:
    """The pattern every boundary test uses.

    The original value is captured rather than written as a literal. What is being asserted is
    that the shared instance is *unchanged*, not what it happens to hold — and this module states
    that the defaults are expected to be retuned, so a literal here would make every retune a
    two-file edit for no added protection.
    """
    original = DEFAULT_THRESHOLDS.trunk_slouch_deg
    strict = dataclasses.replace(DEFAULT_THRESHOLDS, trunk_slouch_deg=original - 5.0)
    assert strict.trunk_slouch_deg == original - 5.0
    assert DEFAULT_THRESHOLDS.trunk_slouch_deg == original
    assert strict.min_visibility == DEFAULT_THRESHOLDS.min_visibility


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_confidence_thresholds_must_be_probabilities(value: float) -> None:
    with pytest.raises(ValueError, match="probability"):
        Thresholds(min_visibility=value)
    with pytest.raises(ValueError, match="probability"):
        Thresholds(min_presence=value)


def test_an_inverted_trunk_band_is_rejected() -> None:
    """Upright above slouch leaves a band nothing can fall into.

    That is the failure this validation exists for: it raises nothing at runtime, produces no bad
    number, and simply means one classification can never be reached. Silent dead code in a rules
    engine is worse than a crash.
    """
    with pytest.raises(ValueError, match="nothing could fall between them"):
        Thresholds(trunk_upright_deg=30.0, trunk_slouch_deg=20.0)


def test_recline_must_be_a_negative_angle() -> None:
    """It is a *backward* lean, and the sign convention is the whole point of the metric."""
    with pytest.raises(ValueError, match="must be negative"):
        Thresholds(trunk_recline_deg=20.0)


def test_the_craniovertebral_ordering_is_enforced_because_smaller_is_worse() -> None:
    """The one threshold in the file where a smaller value means worse posture.

    Every other angular threshold runs the other way, so getting this backwards is an easy and
    entirely silent mistake.
    """
    with pytest.raises(ValueError, match="smaller craniovertebral"):
        Thresholds(cva_forward_head_deg=60.0, cva_borderline_deg=55.0)


def test_equal_craniovertebral_thresholds_are_rejected_too() -> None:
    """Equality is not a harmless edge case: it empties the borderline band.

    With both set to 50, `value < 50` claims the major finding and `value < 50` can never be
    reached again, so `forward_head_borderline` becomes unreachable. That is precisely the silent
    dead rule this validation exists to prevent, so the comparison is strict.
    """
    with pytest.raises(ValueError, match="strictly below"):
        Thresholds(cva_forward_head_deg=50.0, cva_borderline_deg=50.0)


def test_an_inverted_knee_band_is_rejected() -> None:
    with pytest.raises(ValueError, match="knee_seated_min_deg"):
        Thresholds(knee_seated_min_deg=130.0, knee_seated_max_deg=120.0)


def test_a_kneeling_ceiling_that_reaches_into_the_seated_band_is_rejected() -> None:
    """The ordering check the seated-band one did not cover.

    `_describe` tests kneeling first, so raising `knee_kneeling_max_deg` to or above
    `knee_seated_min_deg` does not produce a wrong number — it deletes the tucked-back band
    entirely and relabels part of the seated range as kneeling. Same class of silent dead rule as
    the trunk and craniovertebral checks above.
    """
    with pytest.raises(ValueError, match="knee_kneeling_max_deg"):
        Thresholds(knee_kneeling_max_deg=70.0, knee_seated_min_deg=70.0)


def test_the_view_bands_must_leave_a_non_negative_ambiguous_gap() -> None:
    with pytest.raises(ValueError, match="ambiguous-view band"):
        Thresholds(lateral_view_max_ratio=0.6, frontal_view_min_ratio=0.5)


def test_a_negative_score_penalty_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        Thresholds(score_penalty_per_finding=-5.0)


def test_every_field_has_a_default_so_a_caller_can_override_one_thing() -> None:
    """A required field here would make `Thresholds(trunk_slouch_deg=25)` impossible and push
    every test into restating the whole configuration."""
    for field in dataclasses.fields(Thresholds):
        assert field.default is not dataclasses.MISSING, field.name
