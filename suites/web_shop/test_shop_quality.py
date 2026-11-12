"""Deeper demo only: accessibility, speed and product-detail checks, run with the browser matrix."""
import time

import pytest
from axe_playwright_python.sync_playwright import Axe

from keelward_live import req
from shop import BASE_URL, PASSWORD, RELEASE

pytestmark = pytest.mark.deep
SERIOUS = ("serious", "critical")


def serious_violations(page):
    # The run's own time badge is not part of the store, so it is left out.
    results = Axe().run(page, context={"exclude": [["#kw-live-badge"]]}, options={"resultTypes": ["violations"]})
    return [v for v in results.response["violations"] if v.get("impact") in SERIOUS]


def describe(violations):
    return "; ".join(f"{v['id']} ({v['impact']}, {len(v['nodes'])} element(s))" for v in violations[:4])


@pytest.mark.one_browser
@req("WEB-11", "The sign-in page has no serious accessibility barriers",
     "La page de connexion ne présente pas d’obstacle d’accessibilité grave",
     "No serious or critical issues in an automated WCAG check (axe-core).",
     "Aucun problème grave ou critique lors d’un contrôle WCAG automatisé (axe-core).", "High")
def test_sign_in_page_accessibility(page, step):
    with step("Open the sign-in page", "Ouvrir la page de connexion"):
        page.goto(BASE_URL + "/")
    with step("Run the automated accessibility check", "Lancer le contrôle d’accessibilité automatisé"):
        found = serious_violations(page)
        step.detail(f"{len(found)} serious or critical issue(s)")
        assert not found, f"Accessibility issues: {describe(found)}"


@pytest.mark.one_browser
@req("WEB-12", "The product catalog has no serious accessibility barriers",
     "Le catalogue ne présente pas d’obstacle d’accessibilité grave",
     "No serious or critical issues in an automated WCAG check (axe-core).",
     "Aucun problème grave ou critique lors d’un contrôle WCAG automatisé (axe-core).", "High")
def test_catalog_accessibility(store, step):
    store.login()
    with step("Run the automated accessibility check", "Lancer le contrôle d’accessibilité automatisé"):
        found = serious_violations(store.page)
        step.detail(f"{len(found)} serious or critical issue(s)")
        assert not found, f"Accessibility issues: {describe(found)}"


@pytest.mark.one_browser
@req("WEB-13", "Signing in takes less than 3 seconds",
     "La connexion prend moins de 3 secondes",
     "The catalog appears less than 3 seconds after clicking Login.",
     "Le catalogue s’affiche moins de 3 secondes après le clic sur Login.", "Medium")
def test_sign_in_speed(store, step):
    # In the regression release, the store's built-in slow account stands in for a performance regression.
    user = "performance_glitch_user" if RELEASE == "regression" else "standard_user"
    with step(f"Open the store as {user}", f"Ouvrir la boutique en tant que {user}"):
        store.page.goto(BASE_URL + "/")
        store.page.fill('[data-test="username"]', user)
        store.page.fill('[data-test="password"]', PASSWORD)
    with step("Sign in and time how long the catalog takes", "Se connecter et mesurer l’affichage du catalogue"):
        start = time.monotonic()
        store.page.click('[data-test="login-button"]')
        store.page.locator('[data-test="inventory-list"]').wait_for(timeout=15000)
        seconds = time.monotonic() - start
        step.detail(f"{seconds:.2f} s")
        assert seconds < 3, f"Signing in took {seconds:.1f} seconds"


@req("WEB-14", "Opening a product shows that product’s details",
     "Ouvrir un produit affiche le détail de ce produit",
     "Clicking “Sauce Labs Backpack” opens the page for the backpack.",
     "Cliquer sur « Sauce Labs Backpack » ouvre la page du sac à dos.", "High")
def test_product_details(store, step):
    store.login()
    with step("Open the backpack", "Ouvrir le sac à dos"):
        store.page.click("text=Sauce Labs Backpack")
    with step("Check the product page", "Vérifier la page produit"):
        name = store.page.locator('[data-test="inventory-item-name"]').inner_text()
        assert name == "Sauce Labs Backpack", f"The page shows {name!r}"
