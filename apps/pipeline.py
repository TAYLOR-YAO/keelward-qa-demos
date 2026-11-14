"""Daily sales pipeline (PySpark): the application under test in the Data & Analytics demo.

A fictional online store, Harbor & Co, receives one orders file per day from its shop platform, plus a product
catalog and currency rates. The pipeline follows the medallion layout used on Databricks:

  bronze  the raw files as delivered, with the file each row came from
  silver  clean orders: one row per order, valid values, product category, amount in US dollars,
          business day in the store's time zone (New York); bad rows go to a quarantine table with a reason
  gold    daily revenue and orders, and revenue by category, for the dashboard

Two releases:
  good       the tuned pipeline
  regressed  a "refactor to make the job faster" that drops safeguards: no de-duplication, an inner join to the
             catalog, UTC dates, and no handling of the column the source renamed (it arrives empty)

The code uses only standard PySpark DataFrame functions, so it runs unchanged on Databricks (where bronze,
silver and gold would be Delta tables). The demo runs Spark locally on a few thousand generated orders.
"""
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORE_TZ = "America/New_York"
DAYS = ["2027-03-01", "2027-03-02", "2027-03-03"]
CATEGORIES = {"Kitchen": (8, 60), "Outdoor": (20, 180), "Office": (4, 45), "Audio": (25, 240)}
FX = {"USD": 1.0, "EUR": 1.0842, "CAD": 0.7375}


# ------------------------------------------------------------------ the source data
ISSUES = ("duplicate", "renamed", "new_products", "invalid", "evening", "currencies")


def generate(folder, seed=7, issues=ISSUES, base=1800):
    """Writes the store's source files: three daily order files, one delivered twice, the catalog and the rates.

    What real sources do, on purpose:
      - the 2027-03-02 file is delivered twice (orders_2027-03-02_retry.csv)
      - from 2027-03-03 the shop platform renamed the column unit_price to price
      - a few orders reference two products not yet in the catalog
      - a few rows have a zero quantity or a negative price (test orders, refunds sent as orders)
      - some orders are placed late in the evening, New York time (already the next day in UTC)
      - orders are paid in USD, EUR or CAD
    `issues` turns these on or off (the AI playground lets visitors choose); with all of them on, the files are
    the same as in the nightly tests.
    """
    issues = set(issues)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    products = []
    pid = 100
    for cat, (lo, hi) in CATEGORIES.items():
        for i in range(12):
            pid += 1
            products.append({"product_id": f"P{pid}", "name": f"{cat} item {i + 1}", "category": cat, "list_price": round(rnd.uniform(lo, hi), 2)})
    (folder / "products.json").write_text(json.dumps(products, indent=1), encoding="utf-8")
    (folder / "fx_rates.json").write_text(json.dumps({"base": "USD", "rates": FX}), encoding="utf-8")
    new_products = [{"product_id": "P901", "list_price": 34.0}, {"product_id": "P902", "list_price": 129.0}]

    oid = 500000
    for d, day in enumerate(DAYS):
        rows = []
        local_midnight = datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(hours=-5)))  # EST in March 2027
        for n in range(base + d * base // 12):
            oid += 1
            # Most orders in the day; about 8% between 19:00 and 23:59 New York time (00:00-04:59 UTC the next day).
            evening = rnd.random() < 0.08 and "evening" in issues
            minute = rnd.randint(19 * 60, 23 * 60 + 59) if evening else rnd.randint(7 * 60, 19 * 60 - 1)
            ts = (local_midnight + timedelta(minutes=minute, seconds=rnd.randint(0, 59))).astimezone(timezone.utc)
            p = rnd.choice(new_products) if rnd.random() < 0.012 and "new_products" in issues else rnd.choice(products)
            cur = rnd.choices(["USD", "EUR", "CAD"], [0.78, 0.14, 0.08])[0] if "currencies" in issues else "USD"
            price = round(p["list_price"] / FX[cur], 2)
            qty = rnd.choices([1, 2, 3, 4], [0.7, 0.2, 0.07, 0.03])[0]
            rows.append({"order_id": f"O{oid}", "order_ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "customer_id": f"C{rnd.randint(1000, 4999)}",
                         "product_id": p["product_id"], "quantity": qty, "unit_price": price, "currency": cur})
        for bad in rnd.sample(range(len(rows)), 3):  # test orders and refunds sent as orders
            if "invalid" not in issues:
                rnd.random()
            elif rnd.random() < 0.5:
                rows[bad]["quantity"] = 0
            else:
                rows[bad]["unit_price"] = -abs(rows[bad]["unit_price"])
        renamed = day >= "2027-03-03" and "renamed" in issues
        cols = ["order_id", "order_ts", "customer_id", "product_id", "quantity", "price" if renamed else "unit_price", "currency"]
        names = [f"orders_{day}.csv"] + ([f"orders_{day}_retry.csv"] if day == "2027-03-02" and "duplicate" in issues else [])
        for name in names:
            with open(folder / name, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for r in rows:
                    w.writerow([r["order_id"], r["order_ts"], r["customer_id"], r["product_id"], r["quantity"], r["unit_price"], r["currency"]])
    return folder


def expected(folder):
    """The reference answer, computed without Spark from the files themselves (an independent implementation):
    unique orders, valid values, amounts in USD, business day in New York time."""
    folder = Path(folder)
    seen = {}
    for f in sorted(folder.glob("orders_*.csv")):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                seen[r["order_id"]] = r
    days, valid, invalid, invalid_ids = {}, 0, 0, []
    for r in seen.values():
        qty, price = int(r["quantity"]), float(r.get("unit_price") or r.get("price"))
        if qty < 1 or price <= 0:
            invalid += 1
            invalid_ids.append(r["order_id"])
            continue
        valid += 1
        ts = datetime.strptime(r["order_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        local = ts.astimezone(timezone(timedelta(hours=-5))).date().isoformat()
        d = days.setdefault(local, {"orders": 0, "revenue": 0.0})
        d["orders"] += 1
        d["revenue"] += qty * price * FX[r["currency"]]
    return {"source_rows": sum(1 for f in folder.glob("orders_*.csv") for _ in open(f, encoding="utf-8")) - len(list(folder.glob("orders_*.csv"))),
            "unique_orders": len(seen), "valid": valid, "invalid": invalid, "invalid_ids": sorted(invalid_ids),
            "days": {k: {"orders": v["orders"], "revenue": round(v["revenue"], 2)} for k, v in sorted(days.items())}}


# ------------------------------------------------------------------ Spark
def spark_session():
    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.master("local[2]").appName("keelward-data-demo")
             .config("spark.ui.enabled", "false").config("spark.ui.showConsoleProgress", "false").config("spark.sql.shuffle.partitions", "4")
             .config("spark.sql.session.timeZone", "UTC").config("spark.driver.memory", "1g")
             .config("spark.sql.ansi.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    return spark


ORDER_COLUMNS = {"order_id": "string", "order_ts": "string", "customer_id": "string", "product_id": "string",
                 "quantity": "int", "unit_price": "double", "currency": "string"}
ALIASES = {"price": "unit_price"}  # known renames at the source


def bronze(spark, folder, release="good"):
    """Raw orders, one DataFrame for all files, with the source file name and the columns of the contract."""
    from pyspark.sql import functions as F

    parts = []
    for f in sorted(Path(folder).glob("orders_*.csv")):
        df = spark.read.option("header", True).csv(str(f))
        if release == "good":
            for old, new in ALIASES.items():  # schema evolution handled explicitly
                if old in df.columns and new not in df.columns:
                    df = df.withColumnRenamed(old, new)
            missing = [c for c in ORDER_COLUMNS if c not in df.columns]
            if missing:
                raise ValueError(f"{f.name}: missing columns {missing}")
        df = df.select(*[(F.col(c) if c in df.columns else F.lit(None)).cast(t).alias(c) for c, t in ORDER_COLUMNS.items()])
        parts.append(df.withColumn("source_file", F.lit(f.name)))
    out = parts[0]
    for p in parts[1:]:
        out = out.unionByName(p)
    return out


def silver(spark, bronze_df, folder, release="good"):
    """Clean orders and the quarantine of rows that cannot be trusted."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    products = spark.read.option("multiLine", True).json(str(Path(folder) / "products.json")).select("product_id", "category")
    rates = json.loads((Path(folder) / "fx_rates.json").read_text(encoding="utf-8"))["rates"]
    fx = spark.createDataFrame([(k, float(v)) for k, v in rates.items()], "currency string, usd_rate double")
    df = bronze_df.withColumn("ts", F.to_timestamp("order_ts", "yyyy-MM-dd'T'HH:mm:ss'Z'"))

    if release == "good":  # one row per order: the latest delivery wins
        w = Window.partitionBy("order_id").orderBy(F.col("source_file").desc())
        df = df.withColumn("_n", F.row_number().over(w)).filter("_n = 1").drop("_n")
    reason = (F.when(F.col("unit_price").isNull(), "missing price").when(F.col("quantity").isNull() | (F.col("quantity") < 1), "quantity below 1")
              .when(F.col("unit_price") <= 0, "price not positive").when(~F.col("currency").isin(*rates.keys()), "unknown currency"))
    df = df.withColumn("reject_reason", reason)
    quarantine = df.filter("reject_reason is not null")
    df = df.filter("reject_reason is null").drop("reject_reason")
    if release == "good":
        df = df.join(products, "product_id", "left").withColumn("category", F.coalesce("category", F.lit("Unmapped")))
    else:  # "the catalog always has every product": an inner join
        df = df.join(products, "product_id", "inner")
    df = df.join(fx, "currency", "left").withColumn("amount_usd", F.round(F.col("quantity") * F.col("unit_price") * F.col("usd_rate"), 2))
    if release == "good":
        df = df.withColumn("order_date", F.to_date(F.from_utc_timestamp("ts", STORE_TZ)))
    else:  # the date of the UTC timestamp
        df = df.withColumn("order_date", F.to_date("ts"))
    cols = ["order_id", "order_date", "ts", "customer_id", "product_id", "category", "quantity", "unit_price", "currency", "amount_usd", "source_file"]
    return df.select(*cols), quarantine


def gold(silver_df):
    from pyspark.sql import functions as F

    daily = silver_df.groupBy("order_date").agg(F.count("*").alias("orders"), F.round(F.sum("amount_usd"), 2).alias("revenue_usd"),
                                              F.round(F.avg("amount_usd"), 2).alias("avg_order_usd")).orderBy("order_date")
    by_category = silver_df.groupBy("order_date", "category").agg(F.round(F.sum("amount_usd"), 2).alias("revenue_usd")).orderBy("order_date", "category")
    return daily, by_category


def run(spark, folder, release="good"):
    b = bronze(spark, folder, release).cache()
    s, q = silver(spark, b, folder, release)
    s, q = s.cache(), q.cache()
    daily, by_cat = gold(s)
    return {"bronze": b, "silver": s, "quarantine": q, "daily": daily.cache(), "by_category": by_cat.cache()}


def chart(expected_days, pipeline_days, title):
    """Bar chart of daily revenue: the reference answer next to the pipeline's dashboard number (PNG bytes)."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = sorted(set(expected_days) | set(pipeline_days))
    exp = [expected_days.get(d, 0) / 1000 for d in days]
    got = [pipeline_days.get(d, 0) / 1000 for d in days]
    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=110)
    x = range(len(days))
    ax.bar([i - 0.2 for i in x], exp, width=0.4, label="Reference (from the source files)", color="#1d6fa5")
    ax.bar([i + 0.2 for i in x], got, width=0.4, label="Pipeline (dashboard)", color="#c0392b" if any(abs(a - b) > 0.01 for a, b in zip(exp, got)) else "#17845a")
    for i, (a, b) in enumerate(zip(exp, got)):
        if abs(a - b) > 0.01:
            ax.annotate(f"{(b - a) / a * 100:+.1f}%" if a else "extra day", (i + 0.2, b), ha="center", va="bottom", fontsize=9, color="#c0392b")
    ax.set_xticks(list(x), days)
    ax.set_ylabel("Revenue (thousand USD)")
    ax.set_title(title, fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, max(exp + got + [1]) * 1.28)
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncols=2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def checks(spark, folder, out, ref):
    """The main data checks of the nightly suite, as a short list (used by the AI playground)."""
    from pyspark.sql import functions as F

    b, s, q = out["bronze"], out["silver"], out["quarantine"]
    empty = b.filter(F.col("unit_price").isNull()).count()
    dupes = s.groupBy("order_id").count().filter("count > 1").count()
    diff = s.count() + q.count() - ref["unique_orders"]
    products = spark.read.option("multiLine", True).json(str(Path(folder) / "products.json")).select("product_id")
    unknown = b.join(products, "product_id", "left_anti").filter("quantity >= 1 and unit_price > 0").select("order_id").distinct()
    n_unknown = unknown.count()
    kept = s.join(unknown, "order_id").select("order_id").distinct().count()
    late = s.withColumn("local", F.from_utc_timestamp("ts", STORE_TZ)).filter(F.col("order_date") != F.to_date("local")).count()
    got = {str(r["order_date"]): float(r["revenue_usd"] or 0) for r in out["daily"].collect()}
    wrong_days = [d for d in sorted(set(got) | set(ref["days"])) if abs(got.get(d, 0) - ref["days"].get(d, {}).get("revenue", 0)) > 1]
    return [  # n: how many rows or days are wrong (0 = passed); the website words the result
        {"id": "DQ-01", "name": "Source contract", "passed": empty == 0, "n": empty, "detail": f"{empty:,} rows with an empty price" if empty else "every column filled"},
        {"id": "DQ-02", "name": "No duplicate orders", "passed": dupes == 0, "n": dupes, "detail": f"{dupes:,} orders counted twice" if dupes else "each order once"},
        {"id": "DQ-03", "name": "Rows reconcile", "passed": diff == 0, "n": diff, "detail": f"{diff:+,} rows vs the source" if diff else "in = out + quarantine"},
        {"id": "DQ-05", "name": "New products kept", "passed": kept == n_unknown, "n": n_unknown - kept, "detail": f"{n_unknown - kept:,} orders dropped" if kept < n_unknown else f"{n_unknown:,} kept and flagged"},
        {"id": "DQ-07", "name": "Store business day", "passed": late == 0, "n": late, "detail": f"{late:,} orders on the wrong day" if late else "New York time"},
        {"id": "DQ-09", "name": "Revenue matches the source", "passed": not wrong_days, "n": len(wrong_days), "detail": f"{len(wrong_days)} day(s) wrong" if wrong_days else "every day within $1"},
    ]
