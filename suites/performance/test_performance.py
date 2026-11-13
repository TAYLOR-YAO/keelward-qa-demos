"""Performance tests: response times, errors and throughput per stage of a realistic load profile."""
import io
import math

from keelward_live import req

from .stages import STAGES

BOUNDS = [(0 if i == 0 else STAGES[i - 1][0], end, users, name) for i, (end, users, name) in enumerate(STAGES)]


def pct(values, p):
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, math.ceil(p / 100 * len(v)) - 1))]


def stage(load, name, endpoint=None, settle=2.0, last=None):
    """Requests of one stage (skipping the first seconds while users ramp up), optionally for one endpoint."""
    lo, hi, users, _ = next(b for b in BOUNDS if b[3] == name)
    lo = hi - last if last else lo + settle
    rows = [r for r in load["rows"] if lo <= r[0] < hi and (endpoint is None or r[1] == endpoint)]
    ok = [r[2] for r in rows if r[3]]
    return {"users": users, "requests": len(rows), "errors": sum(1 for r in rows if not r[3]), "p50": pct(ok, 50), "p95": pct(ok, 95),
            "p99": pct(ok, 99), "rps": len(rows) / max(0.1, hi - lo)}


def table(load, names):
    rows = []
    for n in names:
        s = stage(load, n)
        rows.append([n, s["users"], f"{s['rps']:.0f}", f"{s['p50']:.0f} ms", f"{s['p95']:.0f} ms", f"{s['errors'] / max(1, s['requests']) * 100:.1f}%"])
    return {"cols": ["Stage", "Users", "Requests/s", "Median", "95th percentile", "Errors"], "rows": rows}


def chart(load):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    end = STAGES[-1][0]
    secs = list(range(end))
    p95, users = [], []
    for s in secs:
        ok = [r[2] for r in load["rows"] if s <= r[0] < s + 1 and r[3]]
        p95.append(pct(ok, 95) if ok else float("nan"))
        users.append(next(u for lo, hi, u, _ in BOUNDS if lo <= s < hi))
    errors = [sum(1 for r in load["rows"] if s <= r[0] < s + 1 and not r[3]) for s in secs]
    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=110)
    ax.plot(secs, p95, color="#1d6fa5", lw=2, label="95th percentile response time")
    for s, e in zip(secs, errors):
        if e:
            ax.axvspan(s, s + 1, color="#c0392b", alpha=0.18, lw=0)
    ax.set_ylabel("Response time (ms)")
    ax.set_xlabel("Seconds into the test")
    ax.set_ylim(0, max([v for v in p95 if v == v] + [100]) * 1.15)
    for lo, hi, u, name in BOUNDS:
        ax.axvline(lo, color="#9aa7b4", lw=0.8, ls=":")
        ax.text(lo + 0.4, ax.get_ylim()[1] * 0.93, f"{name}\n{u} users", fontsize=7.5, color="#4a5a6a", va="top")
    ax.spines[["top", "right"]].set_visible(False)
    from matplotlib.patches import Patch
    handles = ax.get_legend_handles_labels()[0] + ([Patch(color="#c0392b", alpha=0.18, label="Seconds with failed requests")] if any(errors) else [])
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="center left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


@req("PERF-01", "Pages are fast on a normal day", "Les pages sont rapides un jour normal",
     "With 10 shoppers, 95% of requests answer in under 200 ms.", "Avec 10 clients, 95 % des requêtes répondent en moins de 200 ms.", "High")
def test_normal_day(load, step):
    with step("Run the load test: 10, 50 and 100 shoppers, then back to 10 (about 45 s)", "Lancer le test de charge : 10, 50 puis 100 clients, puis retour à 10 (environ 45 s)"):
        step.data(table=table(load, [b[3] for b in BOUNDS]))
        step.image(chart(load))
        step.detail(f"Locust · {len(load['rows']):,} requests in {load['seconds']} s")
    with step("Check the normal-day stage", "Vérifier l’étape « jour normal »"):
        s = stage(load, "Normal day")
        assert s["p95"] < 200, f"95th percentile is {s['p95']:.0f} ms with 10 shoppers (limit 200 ms)"


@req("PERF-02", "Pages stay fast at the expected peak", "Les pages restent rapides au pic attendu",
     "With 50 shoppers, 95% of requests answer in under 300 ms.", "Avec 50 clients, 95 % des requêtes répondent en moins de 300 ms.", "Critical")
def test_expected_peak(load, step):
    with step("Check every page at the expected peak (50 shoppers)", "Vérifier chaque page au pic attendu (50 clients)"):
        rows, bad = [], []
        for i, ep in enumerate(["Product page", "Search", "Add to cart"]):
            s = stage(load, "Expected peak", ep)
            rows.append([ep, f"{s['requests']:,}", f"{s['p50']:.0f} ms", f"{s['p95']:.0f} ms"])
            if s["p95"] >= 300:
                bad.append(i)
        step.data(table={"cols": ["Page", "Requests", "Median", "95th percentile"], "rows": rows, "bad": bad})
        s = stage(load, "Expected peak")
        assert s["p95"] < 300, f"95th percentile is {s['p95']:.0f} ms with 50 shoppers (limit 300 ms)"


@req("PERF-03", "The sale day does not break the shop", "Le jour des soldes ne casse pas la boutique",
     "With 100 shoppers, fewer than 1% of requests fail and 95% answer in under 800 ms.",
     "Avec 100 clients, moins de 1 % des requêtes échouent et 95 % répondent en moins de 800 ms.", "Critical")
def test_sale_day(load, step):
    with step("Check errors and response times with 100 shoppers", "Vérifier erreurs et temps de réponse avec 100 clients"):
        s = stage(load, "Sale day")
        rate = s["errors"] / max(1, s["requests"]) * 100
        step.data(table={"cols": ["Measure", "Value", "Limit"], "rows": [["Requests", f"{s['requests']:,}", ""], ["Failed (timeout or error)", f"{s['errors']:,} ({rate:.1f}%)", "< 1%"],
                                                                          ["95th percentile", f"{s['p95']:.0f} ms", "< 800 ms"], ["99th percentile", f"{s['p99']:.0f} ms", ""]],
                         "bad": ([1] if rate >= 1 else []) + ([2] if s["p95"] >= 800 else [])})
        assert rate < 1 and s["p95"] < 800, f"With 100 shoppers: {rate:.1f}% of requests failed, 95th percentile {s['p95']:.0f} ms"


@req("PERF-04", "Capacity grows with demand", "La capacité suit la demande",
     "Going from 50 to 100 shoppers serves at least 1.6 times as many requests per second.",
     "Passer de 50 à 100 clients sert au moins 1,6 fois plus de requêtes par seconde.", "High")
def test_throughput_scales(load, step):
    with step("Compare requests served per second at 50 and 100 shoppers", "Comparer les requêtes servies par seconde à 50 et 100 clients"):
        a, b = stage(load, "Expected peak"), stage(load, "Sale day")
        ok_a = (a["requests"] - a["errors"]) / max(1, a["requests"]) * a["rps"]
        ok_b = (b["requests"] - b["errors"]) / max(1, b["requests"]) * b["rps"]
        ratio = ok_b / max(0.1, ok_a)
        step.data(table={"cols": ["Stage", "Users", "Successful requests/s"], "rows": [["Expected peak", 50, f"{ok_a:.0f}"], ["Sale day", 100, f"{ok_b:.0f}"], ["Ratio", "", f"{ratio:.2f}×"]],
                         "bad": [2] if ratio < 1.6 else []})
        assert ratio >= 1.6, f"Doubling the shoppers served only {ratio:.2f}× the requests: the shop has hit a ceiling of about {ok_b:.0f} requests/s"


@req("PERF-05", "Add to cart stays quick at the peak", "L’ajout au panier reste rapide au pic",
     "With 50 shoppers, 95% of add-to-cart requests answer in under 500 ms.", "Avec 50 clients, 95 % des ajouts au panier répondent en moins de 500 ms.", "Critical")
def test_cart_at_peak(load, step):
    with step("Check add to cart at the expected peak", "Vérifier l’ajout au panier au pic attendu"):
        s = stage(load, "Expected peak", "Add to cart")
        step.data(table={"cols": ["Requests", "Median", "95th percentile", "Limit"], "rows": [[f"{s['requests']:,}", f"{s['p50']:.0f} ms", f"{s['p95']:.0f} ms", "500 ms"]],
                         "bad": [0] if s["p95"] >= 500 else []})
        assert s["p95"] < 500, f"Add to cart takes {s['p95']:.0f} ms at the 95th percentile with 50 shoppers"


@req("PERF-06", "The shop recovers after the rush", "La boutique récupère après l’affluence",
     "Once traffic is back to 10 shoppers, 95% of requests answer in under 200 ms again within 5 seconds.",
     "Quand le trafic revient à 10 clients, 95 % des requêtes repassent sous 200 ms en moins de 5 secondes.", "Medium")
def test_recovery(load, step):
    with step("Check the last 5 seconds of the recovery stage", "Vérifier les 5 dernières secondes de la récupération"):
        s = stage(load, "Recovery", last=5)
        step.data(table={"cols": ["Requests", "Median", "95th percentile", "Errors"], "rows": [[f"{s['requests']:,}", f"{s['p50']:.0f} ms", f"{s['p95']:.0f} ms", s["errors"]]],
                         "bad": [0] if s["p95"] >= 200 else []})
        assert s["p95"] < 200, f"Still {s['p95']:.0f} ms at the 95th percentile after the rush"
