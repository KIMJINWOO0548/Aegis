"""Harness arithmetic checks use no reserved held-out seeds."""

from datetime import timedelta

from scripts.validate_signal_review import AT


def test_candidate_metrics_use_actual_emission_and_eligible_background_duration():
    from scripts.validate_signal_combination import summarize
    candidates = [{"id": str(index), "emittedAtUtc": AT + timedelta(seconds=second)}
                  for index, second in enumerate((.5, 1.5, 2.25, 2.27, 2.35))]
    result = summarize(candidates, (2.23, 2.33))
    assert result["matchedOnset"] is True
    assert round(result["onsetDelayMs"], 6) == 20
    assert result["duplicateEmissionsInsideTruth"] == 1
    assert result["lateEmissionsWithin200Ms"] == 1
    assert result["backgroundCandidateCount"] == 2
    assert result["backgroundCandidatesPerMinute"] == 120 / 2.9


def test_artifact_counterpart_has_identical_public_values_but_separate_truth():
    from scripts.validate_signal_combination import fixture
    first = list(fixture("ar09_pulse", 101))
    second = list(fixture("artifact_counterpart", 101))
    assert [rows for rows, _ in first] == [rows for rows, _ in second]
    assert any(truth for _, truth in first)
    assert not any(truth for _, truth in second)
