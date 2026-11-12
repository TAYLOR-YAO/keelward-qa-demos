"""Settings and a small page object for the practice store."""
import os

BASE_URL = os.environ.get("SHOP_URL", "https://www.saucedemo.com").rstrip("/")
PASSWORD = "secret_sauce"  # published on the practice site's login page
USERS = {"stable": "standard_user", "regression": "problem_user"}
RELEASE = os.environ.get("RELEASE", "stable")


class Store:
    """Small page object for the practice store, so tests read like the requirement they check."""

    def __init__(self, page, step):
        self.page, self.step = page, step

    def login(self, user=None, password=PASSWORD):
        user = user or USERS.get(RELEASE, "standard_user")
        with self.step(f"Open the store and sign in as {user}", f"Ouvrir la boutique et se connecter en tant que {user}"):
            self.page.goto(BASE_URL + "/")
            self.page.fill('[data-test="username"]', user)
            self.page.fill('[data-test="password"]', password)
            self.page.click('[data-test="login-button"]')

    def at_catalog(self):
        return self.page.locator('[data-test="inventory-list"]').is_visible()

    def add(self, slug):
        self.page.click(f'[data-test="add-to-cart-{slug}"]')

    def cart_count(self):
        badge = self.page.locator('[data-test="shopping-cart-badge"]')
        return int(badge.inner_text()) if badge.count() else 0
