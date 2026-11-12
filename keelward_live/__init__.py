"""Live reporting for the Keelward QA demos.

Tests describe what they check with @req(...) and narrate their actions with the ``step`` fixture.
While a run is in progress, the plugin publishes each test's status, steps, screenshots and video so
the Keelward website can show the run live. Without a RUN_ID it writes the same events to ./results.
"""
import pytest


def req(id, en, fr, expected_en, expected_fr, severity="Medium"):
    """The requirement a test checks, in English and French, with the defect severity if it fails."""
    return pytest.mark.requirement(id=id, en=en, fr=fr, expected_en=expected_en, expected_fr=expected_fr, severity=severity)
