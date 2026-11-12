"""Regression suite for the practice hotel-booking API: authentication, bookings, validation and contract."""
import pytest
from jsonschema import Draft202012Validator

from booking import ADMIN, new_booking
from keelward_live import req

smoke = pytest.mark.smoke

BOOKING_SCHEMA = {
    "type": "object",
    "required": ["firstname", "lastname", "totalprice", "depositpaid", "bookingdates"],
    "properties": {
        "firstname": {"type": "string"},
        "lastname": {"type": "string"},
        "totalprice": {"type": "number"},
        "depositpaid": {"type": "boolean"},
        "bookingdates": {
            "type": "object",
            "required": ["checkin", "checkout"],
            "properties": {"checkin": {"type": "string", "format": "date"}, "checkout": {"type": "string", "format": "date"}},
        },
        "additionalneeds": {"type": "string"},
    },
}


@smoke
@req("API-01", "The service is up", "Le service répond",
     "The health check answers with a success status.", "Le contrôle de santé répond avec un statut de succès.", "Critical")
def test_health_check(api, step):
    with step("Call the health check", "Appeler le contrôle de santé"):
        r = api.call("GET", "/ping")
        assert 200 <= r.status_code < 300, f"Health check answered HTTP {r.status_code}"


@smoke
@req("API-02", "An administrator can sign in and get a token", "Un administrateur peut se connecter et obtenir un jeton",
     "Valid credentials return a token.", "Des identifiants valides renvoient un jeton.", "Critical")
def test_admin_gets_token(api, step):
    with step("Sign in with the administrator account", "Se connecter avec le compte administrateur"):
        r = api.login()
        assert r.status_code == 200 and api.token, f"No token returned (HTTP {r.status_code}: {r.text[:120]})"


@smoke
@req("API-03", "A booking can be created", "Une réservation peut être créée",
     "The API returns the new booking's id and the saved details.", "L’API renvoie l’identifiant de la réservation et les données enregistrées.", "Critical")
def test_create_booking(api, step):
    data = new_booking()
    with step("Create a booking", "Créer une réservation"):
        r = api.call("POST", "/booking", json=data)
        assert r.status_code in (200, 201), f"Creation answered HTTP {r.status_code}"
    with step("Check the saved details", "Vérifier les données enregistrées"):
        body = r.json()
        assert isinstance(body.get("bookingid"), int), "No booking id in the response"
        assert body["booking"] == data, "The saved booking differs from what was sent"


@smoke
@req("API-04", "A booking can be read back", "Une réservation peut être relue",
     "Reading a booking returns the same details that were saved.", "La lecture renvoie les mêmes données que celles enregistrées.", "High")
def test_read_booking(api, step, booking):
    with step("Read the booking", "Lire la réservation"):
        r = api.call("GET", f"/booking/{booking['bookingid']}")
        assert r.status_code == 200, f"Read answered HTTP {r.status_code}"
        assert r.json() == booking["booking"], "The booking read back differs from the saved one"


@smoke
@req("API-05", "An administrator can update a booking", "Un administrateur peut modifier une réservation",
     "The booking is replaced with the new details.", "La réservation est remplacée par les nouvelles données.", "High")
def test_update_booking(api, step, booking):
    with step("Sign in", "Se connecter"):
        api.login()
    changed = new_booking(firstname="Updated", totalprice=310)
    with step("Replace the booking", "Remplacer la réservation"):
        r = api.call("PUT", f"/booking/{booking['bookingid']}", auth=True, json=changed)
        assert r.status_code == 200, f"Update answered HTTP {r.status_code}"
        assert r.json()["firstname"] == "Updated" and r.json()["totalprice"] == 310, "The update was not applied"


@smoke
@req("API-06", "An administrator can change part of a booking", "Un administrateur peut modifier une partie d’une réservation",
     "Only the fields sent are changed.", "Seuls les champs envoyés sont modifiés.", "Medium")
def test_partial_update(api, step, booking):
    with step("Sign in", "Se connecter"):
        api.login()
    with step("Change the additional needs only", "Modifier uniquement les besoins particuliers"):
        r = api.call("PATCH", f"/booking/{booking['bookingid']}", auth=True, json={"additionalneeds": "Late check-in"})
        assert r.status_code == 200, f"Partial update answered HTTP {r.status_code}"
        body = r.json()
        assert body["additionalneeds"] == "Late check-in", "The field was not changed"
        assert body["lastname"] == booking["booking"]["lastname"], "Another field changed unexpectedly"


@smoke
@req("API-07", "Bookings can be searched by guest name", "Les réservations peuvent être recherchées par nom",
     "Searching by first and last name finds the booking.", "La recherche par prénom et nom trouve la réservation.", "Medium")
def test_search_by_name(api, step, booking):
    b = booking["booking"]
    with step("Search by guest name", "Rechercher par nom du client"):
        r = api.call("GET", "/booking", params={"firstname": b["firstname"], "lastname": b["lastname"]})
        ids = [x["bookingid"] for x in r.json()]
        assert booking["bookingid"] in ids, "The booking was not found by name"


@smoke
@req("API-08", "A deleted booking is gone", "Une réservation supprimée n’existe plus",
     "After deletion, reading the booking returns 404 Not Found.", "Après suppression, la lecture renvoie 404 Not Found.", "High")
def test_delete_booking(api, step, booking):
    with step("Sign in", "Se connecter"):
        api.login()
    with step("Delete the booking", "Supprimer la réservation"):
        r = api.call("DELETE", f"/booking/{booking['bookingid']}", auth=True)
        assert 200 <= r.status_code < 300, f"Delete answered HTTP {r.status_code}"
    with step("Check it can no longer be read", "Vérifier qu’elle ne peut plus être lue"):
        assert api.call("GET", f"/booking/{booking['bookingid']}").status_code == 404, "The deleted booking can still be read"


# ---- Full regression only: negative, validation and contract tests -------------------------------

@req("API-09", "A wrong password is refused with 401 Unauthorized", "Un mauvais mot de passe est refusé avec 401 Unauthorized",
     "The API answers 401 and returns no token.", "L’API répond 401 et ne renvoie aucun jeton.", "High")
def test_wrong_password_is_refused(api, step):
    with step("Sign in with a wrong password", "Se connecter avec un mauvais mot de passe"):
        r = api.call("POST", "/auth", json={**ADMIN, "password": "wrong"})
        assert "token" not in r.text, "A token was returned for a wrong password"
        assert r.status_code == 401, f"Expected 401 Unauthorized, got HTTP {r.status_code} ({r.text[:60]})"


@req("API-10", "Changing a booking without signing in is refused", "Modifier une réservation sans connexion est refusé",
     "The API answers 401 or 403 and the booking is unchanged.", "L’API répond 401 ou 403 et la réservation est inchangée.", "Critical")
def test_update_without_token_is_refused(api, step, booking):
    with step("Try to change the booking without a token", "Tenter de modifier la réservation sans jeton"):
        r = api.call("PUT", f"/booking/{booking['bookingid']}", json=new_booking(firstname="Intruder"))
        assert r.status_code in (401, 403), f"Expected 401 or 403, got HTTP {r.status_code}"
    with step("Check the booking is unchanged", "Vérifier que la réservation est inchangée"):
        assert api.call("GET", f"/booking/{booking['bookingid']}").json()["firstname"] != "Intruder", "The booking was changed without sign-in"


@req("API-11", "A booking with missing fields is rejected with 400 Bad Request", "Une réservation incomplète est refusée avec 400 Bad Request",
     "The API answers 400 with a clear validation error, not a server error.", "L’API répond 400 avec une erreur de validation claire, pas une erreur serveur.", "High")
def test_missing_fields_are_rejected(api, step):
    with step("Create a booking with only a first name", "Créer une réservation avec seulement un prénom"):
        r = api.call("POST", "/booking", json={"firstname": "Incomplete"})
        assert r.status_code == 400, f"Expected 400 Bad Request, got HTTP {r.status_code} ({r.text[:60]})"


@req("API-12", "A check-out date before the check-in date is rejected", "Une date de départ avant la date d’arrivée est refusée",
     "The API answers 400 and does not save the booking.", "L’API répond 400 et n’enregistre pas la réservation.", "High")
def test_checkout_before_checkin_is_rejected(api, step):
    with step("Create a booking that ends before it starts", "Créer une réservation qui se termine avant de commencer"):
        r = api.create(bookingdates={"checkin": "2027-03-14", "checkout": "2027-03-10"})
        assert r.status_code == 400, f"Expected 400, got HTTP {r.status_code}: the invalid booking was accepted"


@req("API-13", "A negative price is rejected", "Un prix négatif est refusé",
     "The API answers 400 and does not save the booking.", "L’API répond 400 et n’enregistre pas la réservation.", "Medium")
def test_negative_price_is_rejected(api, step):
    with step("Create a booking with a price of -100", "Créer une réservation à -100"):
        r = api.create(totalprice=-100)
        assert r.status_code == 400, f"Expected 400, got HTTP {r.status_code}: the negative price was accepted"


@req("API-14", "An unknown booking returns 404 Not Found", "Une réservation inconnue renvoie 404 Not Found",
     "Reading a booking that does not exist answers 404.", "Lire une réservation inexistante répond 404.", "Low")
def test_unknown_booking_returns_404(api, step):
    with step("Read a booking that does not exist", "Lire une réservation qui n’existe pas"):
        r = api.call("GET", "/booking/999999999")
        assert r.status_code == 404, f"Expected 404, got HTTP {r.status_code}"


@req("API-15", "Bookings match the published data contract", "Les réservations respectent le contrat de données publié",
     "Every field has the documented type and dates use YYYY-MM-DD.", "Chaque champ a le type documenté et les dates sont au format AAAA-MM-JJ.", "Medium")
def test_booking_matches_contract(api, step, booking):
    with step("Read the booking and validate it against the schema", "Lire la réservation et la valider avec le schéma"):
        body = api.call("GET", f"/booking/{booking['bookingid']}").json()
        errors = [e.message for e in Draft202012Validator(BOOKING_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER).iter_errors(body)]
        assert not errors, "Contract violations: " + "; ".join(errors[:3])


@req("API-16", "Deleting a booking answers 200 OK or 204 No Content", "Supprimer une réservation répond 200 OK ou 204 No Content",
     "A successful delete uses a standard success status, not 201 Created.", "Une suppression réussie utilise un statut standard, pas 201 Created.", "Low")
def test_delete_status_code(api, step, booking):
    with step("Sign in", "Se connecter"):
        api.login()
    with step("Delete the booking", "Supprimer la réservation"):
        r = api.call("DELETE", f"/booking/{booking['bookingid']}", auth=True)
        assert r.status_code in (200, 204), f"Expected 200 or 204, got HTTP {r.status_code} {r.reason}"


@req("API-17", "Each call answers within 2 seconds", "Chaque appel répond en moins de 2 secondes",
     "Reading the booking list takes less than 2 seconds.", "La lecture de la liste des réservations prend moins de 2 secondes.", "Medium")
def test_response_time(api, step):
    with step("Read the list of bookings", "Lire la liste des réservations"):
        r = api.call("GET", "/booking")
        assert r.status_code == 200, f"List answered HTTP {r.status_code}"
        assert r.elapsed_ms < 2000, f"The list took {r.elapsed_ms} ms"
