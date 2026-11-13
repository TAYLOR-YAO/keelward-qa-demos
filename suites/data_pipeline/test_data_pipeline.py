"""Data-quality tests for the Harbor & Co daily sales pipeline: contract, duplicates, reconciliation, valid values,
referential integrity, currency, business day, freshness and the dashboard totals."""
from pyspark.sql import functions as F

from apps import pipeline
from keelward_live import req


def money(x):
    return f"${x:,.2f}"


@req("DQ-01", "Every source file meets the data contract", "Chaque fichier source respecte le contrat de données",
     "All order files load with every contract column filled, including files where the shop platform renamed a column.",
     "Tous les fichiers de commandes se chargent avec chaque colonne du contrat remplie, y compris quand la plateforme a renommé une colonne.", "Critical")
def test_source_contract(out, step):
    with step("Load the raw files into bronze and check each column of the contract", "Charger les fichiers bruts en bronze et vérifier chaque colonne du contrat"):
        b = out["bronze"]
        rows, bad = [], []
        per_file = b.groupBy("source_file").agg(F.count("*").alias("rows"), *[F.sum(F.col(c).isNull().cast("int")).alias(c) for c in pipeline.ORDER_COLUMNS]).orderBy("source_file").collect()
        for i, r in enumerate(per_file):
            empty = {c: r[c] for c in pipeline.ORDER_COLUMNS if r[c]}
            rows.append([r["source_file"], f"{r['rows']:,}", ", ".join(f"{c}: {n:,} empty" for c, n in empty.items()) or "complete"])
            if empty:
                bad.append(i)
        step.data(table={"cols": ["File", "Rows", "Contract columns"], "rows": rows, "bad": bad})
        assert not bad, "Columns arrived empty: " + "; ".join(f"{rows[i][0]} ({rows[i][2]})" for i in bad) + ". The source renamed unit_price to price."


@req("DQ-02", "Each order appears only once", "Chaque commande n’apparaît qu’une fois",
     "order_id is unique in silver, even when the shop platform delivers the same file twice.",
     "order_id est unique en silver, même quand la plateforme livre deux fois le même fichier.", "Critical")
def test_no_duplicates(out, step):
    with step("Count orders that appear more than once in silver", "Compter les commandes présentes plusieurs fois en silver"):
        dupes = out["silver"].groupBy("order_id").count().filter("count > 1")
        n = dupes.count()
        sample = [[r["order_id"], r["count"]] for r in dupes.orderBy("order_id").limit(5).collect()]
        step.data(table={"cols": ["order_id", "Times in silver"], "rows": sample or [["(none)", "-"]], "bad": list(range(len(sample)))})
        assert n == 0, f"{n:,} orders are counted more than once (the 2027-03-02 file was delivered twice)"


@req("DQ-03", "No order is lost or added on the way", "Aucune commande n’est perdue ni ajoutée en chemin",
     "Unique source orders = clean orders in silver + quarantined orders.", "Commandes sources uniques = commandes propres en silver + commandes en quarantaine.", "High")
def test_reconciliation(out, reference, step):
    with step("Reconcile order counts from the source files to silver and quarantine", "Rapprocher le nombre de commandes des fichiers sources jusqu’à silver et la quarantaine"):
        silver_unique = out["silver"].select("order_id").distinct().count()
        silver_rows = out["silver"].count()
        quarantined = out["quarantine"].count()
        src = reference["unique_orders"]
        diff = silver_rows + quarantined - src
        rows = [["Rows in the delivered files", f"{reference['source_rows']:,}"], ["Unique orders in the files", f"{src:,}"],
                ["Rows in silver", f"{silver_rows:,}"], ["Unique orders in silver", f"{silver_unique:,}"], ["Quarantined", f"{quarantined:,}"],
                ["Difference (silver + quarantine − unique source)", f"{diff:+,}"]]
        step.data(table={"cols": ["Count", "Value"], "rows": rows, "bad": [5] if diff else []})
        missing = src - silver_unique - quarantined
        assert diff == 0, f"Counts do not reconcile: {diff:+,} rows ({max(missing, 0):,} orders missing from silver and quarantine)"


@req("DQ-04", "Only valid orders reach the dashboard", "Seules les commandes valides arrivent au tableau de bord",
     "No silver row has a quantity below 1 or a price that is not positive; such rows are quarantined with a reason.",
     "Aucune ligne silver n’a une quantité inférieure à 1 ou un prix non positif ; ces lignes sont mises en quarantaine avec un motif.", "High")
def test_valid_values(out, reference, step):
    with step("Look for invalid quantities and prices in silver", "Chercher des quantités et prix invalides en silver"):
        bad_rows = out["silver"].filter("quantity < 1 or unit_price <= 0")
        n = bad_rows.count()
        sample = [[r["order_id"], r["quantity"], r["unit_price"], r["source_file"]] for r in bad_rows.limit(5).collect()]
        step.data(table={"cols": ["order_id", "Quantity", "Unit price", "File"], "rows": sample or [["(none)", "-", "-", "-"]], "bad": list(range(len(sample)))})
    with step("Check the quarantine and its reasons", "Vérifier la quarantaine et ses motifs"):
        reasons = [[r["reject_reason"], r["count"]] for r in out["quarantine"].groupBy("reject_reason").count().orderBy("reject_reason").collect()]
        step.data(table={"cols": ["Reason", "Rows"], "rows": reasons or [["(quarantine is empty)", 0]], "bad": [] if reasons else [0]})
        held = out["quarantine"].filter(F.col("order_id").isin(reference["invalid_ids"])).select("order_id").distinct().count()
        assert n == 0, f"{n} invalid orders reached silver (zero quantity or negative price) instead of the quarantine"
        assert held == len(reference["invalid_ids"]), f"Only {held} of {len(reference['invalid_ids'])} invalid orders are in the quarantine"


@req("DQ-05", "Orders for new products are kept and flagged", "Les commandes de nouveaux produits sont conservées et signalées",
     "Orders for products missing from the catalog stay in silver with category Unmapped, so revenue is not lost.",
     "Les commandes de produits absents du catalogue restent en silver avec la catégorie Unmapped, sans perte de chiffre d’affaires.", "High")
def test_unknown_products(out, source, spark, step):
    with step("Find orders whose product is not in the catalog", "Trouver les commandes dont le produit n’est pas au catalogue"):
        products = spark.read.option("multiLine", True).json(str(source / "products.json")).select("product_id")
        unknown_src = out["bronze"].join(products, "product_id", "left_anti").filter("quantity >= 1 and unit_price > 0").select("order_id").distinct()
        n_src = unknown_src.count()
        kept = out["silver"].join(unknown_src, "order_id").select("order_id").distinct().count()
        flagged = out["silver"].filter("category = 'Unmapped'").select("order_id").distinct().count()
        rows = [["Valid source orders for unknown products", f"{n_src:,}"], ["Kept in silver", f"{kept:,}"], ["Flagged Unmapped", f"{flagged:,}"]]
        step.data(table={"cols": ["Count", "Value"], "rows": rows, "bad": [1] if kept < n_src else []})
        assert kept == n_src, f"{n_src - kept:,} orders for new products (P901, P902) were silently dropped by the join"


@req("DQ-06", "Revenue is in US dollars", "Le chiffre d’affaires est en dollars US",
     "Orders paid in EUR and CAD are converted with the day's rate before they are added up.",
     "Les commandes payées en EUR et CAD sont converties au taux du jour avant d’être additionnées.", "Critical")
def test_currency(out, step):
    with step("Recompute the amount of foreign-currency orders", "Recalculer le montant des commandes en devises"):
        rates = pipeline.FX
        sample = out["silver"].filter("currency in ('EUR','CAD') and quantity >= 1 and unit_price > 0").orderBy("order_id").limit(6).collect()
        rows, bad = [], []
        for i, r in enumerate(sample):
            want = round(r["quantity"] * r["unit_price"] * rates[r["currency"]], 2)
            rows.append([r["order_id"], f"{r['quantity']} × {r['unit_price']} {r['currency']}", money(want), money(r["amount_usd"])])
            if abs(want - r["amount_usd"]) > 0.01:
                bad.append(i)
        step.data(table={"cols": ["Order", "Paid", "Expected (USD)", "Pipeline"], "rows": rows, "bad": bad})
        assert sample, "No foreign-currency orders found"
        assert not bad, f"{len(bad)} of {len(sample)} foreign-currency orders were added up without conversion to USD"


@req("DQ-07", "Orders count on the store's business day", "Les commandes comptent pour le jour ouvré du magasin",
     "Orders placed in the evening in New York count on that day, not on the next UTC day.",
     "Les commandes passées le soir à New York comptent pour ce jour-là, pas pour le jour UTC suivant.", "Medium")
def test_business_day(out, step):
    with step("Check orders placed after 19:00 New York time", "Vérifier les commandes passées après 19 h, heure de New York"):
        late = out["silver"].withColumn("local", F.from_utc_timestamp("ts", pipeline.STORE_TZ)).filter(F.hour("local") >= 19)
        sample = late.orderBy("order_id").limit(6).collect()
        rows, bad = [], []
        for i, r in enumerate(sample):
            want = r["local"].date()
            rows.append([r["order_id"], r["ts"].strftime("%Y-%m-%d %H:%M UTC"), r["local"].strftime("%Y-%m-%d %H:%M"), str(want), str(r["order_date"])])
            if r["order_date"] != want:
                bad.append(i)
        wrong = late.filter(F.col("order_date") != F.to_date("local")).count()
        step.data(table={"cols": ["Order", "Placed (UTC)", "New York time", "Business day", "Pipeline day"], "rows": rows, "bad": bad})
        assert wrong == 0, f"{wrong:,} evening orders are counted on the next day (the pipeline uses UTC dates)"


@req("DQ-08", "The dashboard shows only days that have happened", "Le tableau de bord n’affiche que des jours passés",
     "The latest business day in gold is the last batch day, 2027-03-03.", "Le dernier jour ouvré en gold est celui du dernier lot, 2027-03-03.", "Medium")
def test_freshness(out, step):
    with step("Read the days in the gold table", "Lire les jours de la table gold"):
        days = [str(r["order_date"]) for r in out["daily"].collect()]
        step.data(table={"cols": ["Days in gold"], "rows": [[d] for d in days], "bad": [i for i, d in enumerate(days) if d not in pipeline.DAYS]})
        assert days == pipeline.DAYS, f"Gold has the days {', '.join(days)} instead of {', '.join(pipeline.DAYS)}"


@req("DQ-09", "Daily revenue on the dashboard matches the source", "Le chiffre d’affaires du tableau de bord correspond à la source",
     "Each day's revenue in gold is within $1 of a reference computed independently from the source files.",
     "Le chiffre d’affaires de chaque jour en gold est à 1 $ près celui d’une référence calculée indépendamment à partir des fichiers sources.", "Critical")
def test_revenue_reconciles(out, reference, step):
    with step("Compare gold with a reference computed without Spark", "Comparer gold à une référence calculée sans Spark"):
        got = {str(r["order_date"]): (r["orders"], float(r["revenue_usd"] or 0)) for r in out["daily"].collect()}
        exp = reference["days"]
        rows, bad = [], []
        for i, d in enumerate(sorted(set(exp) | set(got))):
            e, g = exp.get(d, {"orders": 0, "revenue": 0.0}), got.get(d, (0, 0.0))
            diff = g[1] - e["revenue"]
            rows.append([d, f"{e['orders']:,}", f"{g[0]:,}", money(e["revenue"]), money(g[1]), f"{diff:+,.2f}"])
            if abs(diff) > 1 or e["orders"] != g[0]:
                bad.append(i)
        step.data(table={"cols": ["Day", "Orders (ref.)", "Orders (gold)", "Revenue (ref.)", "Revenue (gold)", "Difference"], "rows": rows, "bad": bad})
        step.image(pipeline.chart({d: v["revenue"] for d, v in exp.items()}, {d: v[1] for d, v in got.items()},
                                  "Daily revenue: reference vs dashboard"))
        total_e, total_g = sum(v["revenue"] for v in exp.values()), sum(v[1] for v in got.values())
        assert not bad, f"{len(bad)} day(s) wrong on the dashboard; total {money(total_g)} instead of {money(total_e)} ({(total_g - total_e) / total_e * 100:+.1f}%)"
