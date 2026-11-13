"""AI demo: support ticket triage (classification), checked against labelled messages.

RELEASE=good tests the tuned classifier; RELEASE=regressed tests a release whose prompt was "cleaned up".
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from apps import triage

RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


@pytest.fixture
def classify(step):
    """Classifies several messages in parallel; returns parsed results (None when the output is invalid)."""
    def run(texts):
        with ThreadPoolExecutor(max_workers=6) as pool:
            raws = list(pool.map(lambda t: triage.classify(t, RELEASE, step=step), texts))
        return [triage.parse(r) for r in raws], raws
    return run
