"""AI demo: "Ask Keelward", a RAG chatbot answering from the Keelward website, judged by DeepEval.

RELEASE=good tests the tuned release; RELEASE=regressed tests a release with a weaker search and a
"more persuasive" prompt. The tests are identical; only the release under test changes.
"""
import os

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")
os.environ.setdefault("DEEPEVAL_RESULTS_FOLDER", "")

import pytest  # noqa: E402

from apps import ask_keelward  # noqa: E402
from keelward_live.ai import Judge  # noqa: E402

RELEASE = os.environ.get("RELEASE", "good") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


@pytest.fixture
def judge(step):
    return Judge(step)


@pytest.fixture
def chat(step):
    """Asks the chatbot under test and records the question, answer and sources on the step."""
    def ask(question):
        with step("Ask the chatbot", "Poser la question au chatbot"):
            r = ask_keelward.ask(question, RELEASE, step=step)
            step.data(question=question, answer=r["answer"],
                      sources=[f"{s['page']} › {s['heading']}" for s in r["sources"]])
        return r
    return ask
