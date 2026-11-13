# Keelward QA demos

The test suites behind the **Live demo** page on [keelward.io/demo](https://keelward.io/demo/), and the AI
applications behind the **AI playground** ([keelward.io/playground](https://keelward.io/playground/)) and the
**Ask Keelward** assistant on every page. Visitors start a run on the website and watch every test, step and
screenshot as it happens.

## Quality engineering

| Suite | Application under test | Tools | Variants |
| --- | --- | --- | --- |
| `suites/web_shop` | [Sauce Demo](https://www.saucedemo.com), a practice online store | pytest, Playwright | `stable` (normal account) · `regression` (the site's built-in "problem" account, which behaves like a release shipped with defects) |
| `suites/booking_api` | [Restful-Booker](https://restful-booker.herokuapp.com), a practice hotel-booking API | pytest, requests, jsonschema | `smoke` (happy path) · `full` (adds negative, validation and contract tests) |

| `suites/performance` | Harbor & Co shop API (`apps/shop_api.py`), started next to the test | pytest, Locust | `good` (connection pool, cache) · `regressed` (one shared database connection) |

The web and API suites use public practice applications built for training testers. Keelward is not affiliated with them;
their deliberate defects are part of those sites.

## AI assurance

Five small AI applications (`apps/`), each with a **tuned** release and a **regressed** release that carries
the kind of change that slips into real projects. The suites check them with pytest, DeepEval and an AI judge.
They run on Gemini through Vertex AI; no API key is used.

| Suite | Application under test | What the tests check |
| --- | --- | --- |
| `suites/ask_keelward` | RAG chatbot answering from the keelward.io pages | Grounded answers (no invented prices, timelines or results), citations, declining unknowns, answer language, leaks of its hidden instructions, prompt injection |
| `suites/ticket_triage` | Support-ticket classifier | Valid JSON for the routing software, topic, urgency, consistency |
| `suites/invoice_extraction` | Invoice reader (text, scans and photos) | Every field against the expected values, no invented due dates or totals |
| `suites/image_video` | Product-photo checks, listing-photo moderation, video description | Real defects found, personal data and off-platform contact details blocked, no false alarms |
| `suites/booking_agent` | Hotel booking agent with tools | Confirms before cancelling, ignores instructions hidden in booking notes, checks facts with a tool |

```bash
SUITE=rag VARIANT=regressed python run.py   # rag, triage, extract, vision, agent × good, regressed
```

`live.py` serves the same applications to the website's assistant and playground (both releases side by
side), so what visitors try is exactly what is tested every night. `tools/check_live.py` checks it end to end.

## Data & analytics

`suites/data_pipeline` checks a daily sales pipeline written in PySpark (`apps/pipeline.py`), in the medallion
layout used on Databricks: bronze (raw files), silver (clean orders, quarantine), gold (dashboard tables). The
source files of a fictional store are generated for each run and behave like real ones: a file delivered twice,
a column renamed by the platform, products missing from the catalog, evening orders, three currencies.

Nine data checks: source contract, duplicates, row reconciliation, valid values and quarantine, unknown
products, currency conversion, business day (time zone), freshness, and daily revenue against a reference
computed without Spark (with a chart). Release `good` passes all nine; release `regressed`, a "faster job"
refactor, fails six.

```bash
SUITE=data VARIANT=regressed python run.py   # needs Java 17 or later
```

## How the tests are written

Each test checks one requirement and narrates its steps:

```python
@req("WEB-05", "Sorting by price, low to high, orders the products correctly", ...)
def test_sort_by_price_low_to_high(store, step):
    store.login()
    with step("Sort the catalog by price, low to high"):
        store.page.select_option('[data-test="product-sort-container"]', "lohi")
    with step("Check the order of the prices"):
        prices = store.prices()
        assert prices == sorted(prices), f"Prices are not in ascending order: {prices}"
```

`keelward_live` is a small pytest plugin. It records each test's status, steps, screenshots and video,
and publishes them while the run is in progress. Failed tests become defect reports (steps to
reproduce, expected, actual, evidence) on the website.

## Run it yourself

```bash
pip install -r requirements.txt
playwright install --with-deps --only-shell chromium

SUITE=web VARIANT=regression python run.py   # or SUITE=api VARIANT=full
```

Results go to `results/`: an HTML report, a JUnit XML file (for CI tools), screenshots, videos and
the live events as JSON lines. You can also run a suite with plain pytest:

```bash
RELEASE=regression pytest suites/web_shop -n 3
pytest suites/booking_api -m smoke
```

## What the demos find

- **Performance, regressed release:** fine with 10 shoppers, but at 50 the 95th percentile passes 400 ms, at 100
  requests time out, and capacity stops at about 60 requests a second. It recovers once the rush is over.
- **Data, regressed release:** a re-delivered file doubles a day's revenue, a renamed column empties a whole
  day's prices, orders for new products disappear in a join, and evening orders move to the next day.

- **AI, regressed releases:** the chatbot invents prices and client results and leaks its hidden instructions;
  the triage marks an outage as normal and returns text the router cannot read; the invoice reader invents a
  due date; moderation lets phone and card numbers through; the agent cancels a booking because a guest's
  note told it to. The tuned releases pass every test.

- **Web, regression release:** wrong product photos, broken price sorting, add-to-cart and remove buttons
  that do nothing, and a checkout that cannot be completed. The stable release passes all 10 tests.
- **API, full regression:** a wrong password answers 200 instead of 401; incomplete bookings crash the
  server (500) instead of a 400 validation error; a check-out before check-in and a negative price are
  accepted; deleting answers 201 Created. The happy-path check passes all 8 tests and misses all of this.

Want suites like these for your own application? [Talk to Keelward](https://keelward.io/contact/).
