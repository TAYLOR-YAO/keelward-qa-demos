"""AI demo: invoice extraction from text and scanned images, checked field by field against the right answer.

RELEASE=good tests the strict extractor; RELEASE=regressed tests a release told to "fill gaps sensibly".
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from apps import extract

RELEASE = os.environ.get("RELEASE") if os.environ.get("RELEASE") in ("good", "regressed") else "good"


@pytest.fixture
def run_extraction(step):
    """Extracts the given invoices in parallel and records a field-by-field comparison."""
    def run(ids, fields=extract.FIELDS):
        invoices = [i for i in extract.SAMPLES if i["id"] in ids]
        with ThreadPoolExecutor(max_workers=4) as pool:
            raws = list(pool.map(lambda inv: extract.extract(inv, RELEASE, step=step), invoices))
        parsed = [extract.parse(r) for r in raws]
        step.raws = raws
        rows, bad = [], []
        for inv, p in zip(invoices, parsed):
            for f in fields:
                want = inv["expected"][f]
                got = getattr(p, f) if p else "invalid output"
                ok = p is not None and extract.same(f, got, want)
                if not ok:
                    bad.append(len(rows))
                rows.append([f"{inv['id']} · {inv['label']}", f, "—" if want is None else str(want), "—" if got in (None, "") else str(got)])
        step.data(table={"cols": ["Invoice", "Field", "Expected", "Got"], "rows": rows, "bad": bad})
        return invoices, parsed, bad, rows
    return run
