"""Data & Analytics demo: data-quality tests on a daily sales pipeline (PySpark, medallion layout).

RELEASE=good tests the tuned pipeline; RELEASE=regressed tests a "faster job" refactor that dropped its safeguards.
The source files are generated fresh for each run (a fictional store, Harbor & Co); the pipeline runs once and
every test checks its output against the source files and a reference computed without Spark.
"""
import os
import tempfile

import pytest

from apps import pipeline

RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


@pytest.fixture(scope="session")
def source():
    return pipeline.generate(tempfile.mkdtemp(prefix="harbor-"))


@pytest.fixture(scope="session")
def reference(source):
    return pipeline.expected(source)


@pytest.fixture(scope="session")
def spark():
    s = pipeline.spark_session()
    yield s
    s.stop()


@pytest.fixture(scope="session")
def out(spark, source):
    return pipeline.run(spark, source, RELEASE)
