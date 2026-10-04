"""The scheduled workflow, checked as configuration rather than trusted.

These are cheap structural assertions about daily-intelligence.yml. They
exist because a workflow is only exercised in production, on a schedule,
where a mistake is invisible until the day it matters.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = pathlib.Path(".github/workflows/daily-intelligence.yml")

# Expressions that override the implicit success() check GitHub applies to
# every step condition.
STATUS_FUNCTIONS = ("always()", "failure()", "cancelled()", "success()")


@pytest.fixture(scope="module")
def steps():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]["intelligence"]["steps"]


def test_steps_meant_to_run_after_a_failure_say_so(steps):
    """The bug this file was written for.

    "Record the failure" and "Commit the failure record" both read
    `steps.run.outcome == 'failure'` with no status function. GitHub applies
    an implicit success() to any condition that lacks one, so both were
    skipped on every failure they existed for - runs #46 and #48 died in the
    pipeline step and recorded nothing, and the reason had to be chased
    through the API instead of being committed to the repository.
    """
    offenders = []
    for step in steps:
        condition = str(step.get("if", ""))
        if "failure" not in condition:
            continue
        if not any(fn in condition for fn in STATUS_FUNCTIONS):
            offenders.append(step.get("name"))

    assert not offenders, (
        "these steps run only on failure but carry an implicit success(), "
        f"so they will be skipped exactly when needed: {offenders}"
    )


def test_the_failure_record_is_still_wired_up(steps):
    names = [s.get("name") for s in steps]

    assert "Record the failure" in names
    assert "Commit the failure record" in names
    assert "Keep the run log" in names


def test_the_run_log_is_kept_whatever_happens(steps):
    """The artifact is the fallback when the committed record is not enough."""
    step = next(s for s in steps if s.get("name") == "Keep the run log")

    assert any(fn in str(step.get("if", "")) for fn in STATUS_FUNCTIONS)


def test_the_heartbeat_is_only_written_on_success(steps):
    """A heartbeat after a failed run would say the schedule is healthy."""
    step = next(s for s in steps if s.get("name") == "Record the heartbeat")

    assert "success" in str(step.get("if", ""))


def test_every_api_key_reaches_the_pipeline_step(steps):
    """A key configured as a secret but not passed through is silent."""
    step = next(s for s in steps if s.get("name") == "Run the intelligence pipeline")
    env = step.get("env") or {}

    for variable in ("GEMINI_API_KEY", "YOUTUBE_API_KEY",
                     "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        assert variable in env, f"{variable} is never passed to the run"
        assert "secrets." in str(env[variable])
