"""Regression suite for the practice web store: sign-in, catalog, cart and checkout."""
from keelward_live import req

from shop import BASE_URL


@req("WEB-01", "A customer can sign in and see the product catalog",
     "Un client peut se connecter et voir le catalogue",
     "After signing in, the product catalog is displayed.",
     "Après la connexion, le catalogue des produits s’affiche.", "Critical")
def test_sign_in_shows_catalog(store, step):
    store.login()
    with step("Check the product catalog is shown", "Vérifier que le catalogue s’affiche"):
        assert store.at_catalog(), "The product catalog did not appear after signing in"
        count = store.page.locator('[data-test="inventory-item"]').count()
        assert count == 6, f"Expected 6 products in the catalog, found {count}"


@req("WEB-02", "A locked account is refused with a clear message",
     "Un compte bloqué est refusé avec un message clair",
     "Sign-in is refused and the message says the account is locked.",
     "La connexion est refusée et le message indique que le compte est bloqué.", "High")
def test_locked_account_is_refused(store, step):
    store.login(user="locked_out_user")
    with step("Check the error message", "Vérifier le message d’erreur"):
        error = store.page.locator('[data-test="error"]').inner_text()
        assert "locked out" in error.lower(), f"Unexpected message: {error!r}"
        assert not store.at_catalog(), "A locked account reached the catalog"


@req("WEB-03", "A wrong password is refused",
     "Un mauvais mot de passe est refusé",
     "Sign-in is refused and an error explains the credentials do not match.",
     "La connexion est refusée et une erreur indique que les identifiants ne correspondent pas.", "High")
def test_wrong_password_is_refused(store, step):
    store.login(password="not-the-password")
    with step("Check the error message", "Vérifier le message d’erreur"):
        error = store.page.locator('[data-test="error"]').inner_text()
        assert "do not match" in error.lower(), f"Unexpected message: {error!r}"


@req("WEB-04", "Each product shows its own photo",
     "Chaque produit affiche sa propre photo",
     "The 6 products show 6 different photos.",
     "Les 6 produits affichent 6 photos différentes.", "Medium")
def test_each_product_has_its_own_photo(store, step):
    store.login()
    with step("Compare the product photos", "Comparer les photos des produits"):
        srcs = store.page.locator('[data-test="inventory-item"] img').evaluate_all("els => els.map(e => e.getAttribute('src'))")
        distinct = len(set(srcs))
        assert distinct == len(srcs), f"Only {distinct} different photo(s) for {len(srcs)} products"


@req("WEB-05", "Sorting by price, low to high, orders the products correctly",
     "Le tri par prix croissant classe correctement les produits",
     "Prices appear from lowest to highest.",
     "Les prix apparaissent du plus bas au plus élevé.", "Medium")
def test_sort_by_price_low_to_high(store, step):
    store.login()
    with step("Sort the catalog by price, low to high", "Trier le catalogue par prix croissant"):
        store.page.select_option('[data-test="product-sort-container"]', "lohi")
    with step("Check the order of the prices", "Vérifier l’ordre des prix"):
        prices = store.prices()
        assert prices == sorted(prices), f"Prices are not in ascending order: {prices}"


@req("WEB-06", "Adding three products updates the cart to 3",
     "Ajouter trois produits met le panier à 3",
     "The cart badge shows 3 after adding three products.",
     "Le badge du panier indique 3 après l’ajout de trois produits.", "High")
def test_add_three_products_to_cart(store, step):
    store.login()
    for slug, label in [("sauce-labs-backpack", "backpack"), ("sauce-labs-bolt-t-shirt", "T-shirt"), ("sauce-labs-fleece-jacket", "fleece jacket")]:
        with step(f"Add the {label} to the cart", f"Ajouter l’article « {label} » au panier"):
            store.add(slug)
    with step("Check the cart count", "Vérifier le nombre d’articles du panier"):
        assert store.cart_count() == 3, f"The cart shows {store.cart_count()} item(s) instead of 3"


@req("WEB-07", "A product can be removed from the cart",
     "Un produit peut être retiré du panier",
     "After removing one of two products, the cart shows 1.",
     "Après le retrait d’un des deux produits, le panier indique 1.", "Medium")
def test_remove_product_from_cart(store, step):
    store.login()
    with step("Add two products", "Ajouter deux produits"):
        store.add("sauce-labs-backpack")
        store.add("sauce-labs-bike-light")
    with step("Remove the backpack", "Retirer le sac à dos"):
        store.page.click('[data-test="remove-sauce-labs-backpack"]')
    with step("Check the cart count", "Vérifier le nombre d’articles du panier"):
        assert store.cart_count() == 1, f"The cart shows {store.cart_count()} item(s) instead of 1"


@req("WEB-08", "Checkout asks for the missing postal code",
     "Le paiement signale le code postal manquant",
     "Continuing without a postal code shows “Postal Code is required”.",
     "Continuer sans code postal affiche « Postal Code is required ».", "Low")
def test_checkout_requires_postal_code(store, step):
    store.login()
    with step("Add a product and start checkout", "Ajouter un produit et commencer le paiement"):
        store.add("sauce-labs-backpack")
        store.page.click('[data-test="shopping-cart-link"]')
        store.page.click('[data-test="checkout"]')
    with step("Enter a name but no postal code", "Saisir un nom sans code postal"):
        store.page.fill('[data-test="firstName"]', "Ada")
        store.page.fill('[data-test="lastName"]', "Lovelace")
        store.page.click('[data-test="continue"]')
    with step("Check the validation message", "Vérifier le message de validation"):
        error = store.page.locator('[data-test="error"]').inner_text()
        assert "postal code is required" in error.lower(), f"Unexpected message: {error!r}"


@req("WEB-09", "A customer can complete a purchase",
     "Un client peut finaliser un achat",
     "The order is confirmed with “Thank you for your order!”.",
     "La commande est confirmée par « Thank you for your order! ».", "Critical")
def test_complete_a_purchase(store, step):
    store.login()
    with step("Add a product and open the cart", "Ajouter un produit et ouvrir le panier"):
        store.add("sauce-labs-backpack")
        store.page.click('[data-test="shopping-cart-link"]')
        store.page.click('[data-test="checkout"]')
    with step("Fill in the delivery details", "Saisir les informations de livraison"):
        store.page.fill('[data-test="firstName"]', "Ada")
        store.page.fill('[data-test="lastName"]', "Lovelace")
        store.page.fill('[data-test="postalCode"]', "30303")
        store.page.click('[data-test="continue"]')
    with step("Confirm the order", "Confirmer la commande"):
        error = store.page.locator('[data-test="error"]')
        assert not error.count(), f"Checkout was blocked: {error.inner_text()!r}"
        store.page.click('[data-test="finish"]')
    with step("Check the confirmation", "Vérifier la confirmation"):
        header = store.page.locator('[data-test="complete-header"]').inner_text()
        assert "thank you for your order" in header.lower(), f"Unexpected confirmation: {header!r}"


@req("WEB-10", "Signing out returns to the sign-in page",
     "La déconnexion ramène à la page de connexion",
     "After signing out, the sign-in form is shown.",
     "Après la déconnexion, le formulaire de connexion s’affiche.", "Medium")
def test_sign_out(store, step):
    store.login()
    with step("Open the menu and sign out", "Ouvrir le menu et se déconnecter"):
        store.page.click("#react-burger-menu-btn")
        store.page.click('[data-test="logout-sidebar-link"]')
    with step("Check the sign-in form is shown", "Vérifier que le formulaire de connexion s’affiche"):
        assert store.page.locator('[data-test="login-button"]').is_visible(), "The sign-in form is not shown"
        assert store.page.url.rstrip("/") == BASE_URL, f"Unexpected page after sign-out: {store.page.url}"
