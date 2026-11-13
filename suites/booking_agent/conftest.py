"""AI demo: a booking agent that calls tools on the public practice booking API.

RELEASE=good tests the agent with safety rules; RELEASE=regressed tests a "faster, fewer questions" release.
"""
import os

import pytest

from apps import agent

RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


@pytest.fixture
def tools():
    t = agent.BookingTools()
    yield t
    t.cleanup()


@pytest.fixture
def talk(tools, step):
    """Talks to the agent and records the conversation and every tool call it made."""
    def run(messages):
        with step("Talk to the booking agent", "Dialoguer avec l’agent de réservation"):
            replies = agent.chat(tools, messages, RELEASE, step=step)
            step.data(question="\n".join(f"Guest: {m}" for m in messages), answer="\n\n".join(replies), table=agent.calls_table(tools))
        return replies
    return run
