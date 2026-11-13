"""Evaluation suite for invoice extraction: field accuracy on text and scans, no invented data, consistent amounts."""
from apps import extract
from keelward_live import req


def mismatches(rows, bad, n=4):
    return "; ".join(f"{rows[i][0].split(' · ')[0]}.{rows[i][1]}: expected {rows[i][2]}, got {rows[i][3]}" for i in bad[:n])


@req("EXT-01", "Text invoices are extracted exactly", "Les factures texte sont extraites exactement",
     "Every field of the two text invoices matches.", "Chaque champ des deux factures texte correspond.", "High")
def test_text_invoices(run_extraction, step):
    with step("Extract the two text invoices", "Extraire les deux factures texte"):
        _, _, bad, rows = run_extraction(["A", "B"])
        step.score("Field accuracy", round(1 - len(bad) / len(rows), 2), 1.0, not bad)
        assert not bad, f"{len(bad)} wrong field(s): {mismatches(rows, bad)}"


@req("EXT-02", "Scanned invoices are extracted exactly", "Les factures scannées sont extraites exactement",
     "Every field of the two scanned images matches, including the skewed, low-quality scan.",
     "Chaque champ des deux images correspond, y compris le scan penché et de mauvaise qualité.", "High")
def test_scanned_invoices(run_extraction, step):
    with step("Extract the two scanned invoices (images)", "Extraire les deux factures scannées (images)"):
        _, _, bad, rows = run_extraction(["C", "D"])
        step.score("Field accuracy", round(1 - len(bad) / len(rows), 2), 1.0, not bad)
        assert not bad, f"{len(bad)} wrong field(s): {mismatches(rows, bad)}"


@req("EXT-03", "Missing information is left empty, not invented", "Une information absente reste vide, sans invention",
     "The invoice without a due date gets no due date.", "La facture sans date d’échéance n’en reçoit pas.", "Critical")
def test_missing_not_invented(run_extraction, step):
    with step("Extract the invoice that has no due date", "Extraire la facture sans date d’échéance"):
        _, (p,), bad, rows = run_extraction(["B"], fields=["due_date"])
        assert p is not None, "The reply is not valid data"
        assert not bad, f"A due date was invented: {p.due_date}"


@req("EXT-04", "Extracted amounts add up", "Les montants extraits sont cohérents",
     "For every invoice, subtotal + tax = total (to the cent).", "Pour chaque facture, sous-total + taxe = total (au centime près).", "High")
def test_amounts_add_up(run_extraction, step):
    with step("Extract all four invoices and check the arithmetic", "Extraire les quatre factures et vérifier les calculs"):
        invoices, parsed, _, _ = run_extraction(["A", "B", "C", "D"], fields=["subtotal", "tax", "total"])
        wrong = [inv["id"] for inv, p in zip(invoices, parsed)
                 if not p or None in (p.subtotal, p.tax, p.total) or abs(p.subtotal + p.tax - p.total) > 0.01]
        assert not wrong, f"Amounts do not add up for invoice(s) {', '.join(wrong)}"


@req("EXT-05", "French amounts and dates are converted correctly", "Les montants et dates en français sont convertis correctement",
     "“1 250,00 €” becomes 1250.00 EUR and “12 octobre 2026” becomes 2026-10-12.",
     "« 1 250,00 € » devient 1250.00 EUR et « 12 octobre 2026 » devient 2026-10-12.", "Medium")
def test_french_formats(run_extraction, step):
    with step("Extract the French invoice", "Extraire la facture française"):
        _, _, bad, rows = run_extraction(["C"], fields=["invoice_date", "due_date", "currency", "subtotal", "tax", "total"])
        assert not bad, f"{len(bad)} wrong field(s): {mismatches(rows, bad)}"


@req("EXT-06", "Every reply is valid, usable data", "Chaque réponse est une donnée valide et exploitable",
     "All four replies are valid JSON with the expected fields and types.", "Les quatre réponses sont du JSON valide avec les champs et types attendus.", "High")
def test_valid_output(run_extraction, step):
    with step("Check the format of every reply", "Vérifier le format de chaque réponse"):
        invoices, _, _, _ = run_extraction(["A", "B", "C", "D"], fields=["invoice_number"])
        invalid = [inv["id"] for inv, raw in zip(invoices, step.raws) if extract.parse(raw, strict=True) is None]
        assert not invalid, f"Invalid replies for invoice(s) {', '.join(invalid)}"
