"""Evaluation suite for image and video AI: visual QA, photo moderation and video review."""
import pytest

from apps import vision
from keelward_live import req

from conftest import RELEASE

TOLERANCE = 2.5  # seconds
video = pytest.mark.xdist_group("video")      # one recording per worker, shared by the three video checks
catalog = pytest.mark.xdist_group("catalog")


@catalog
@req("VIS-01", "Broken product photos are flagged", "Les photos produit défectueuses sont signalées",
     "On the broken catalog (same photo for every product), the assistant fails the page and counts at most 2 distinct photos.",
     "Sur le catalogue défectueux (même photo pour tous les produits), l’assistant rejette la page et compte au plus 2 photos distinctes.", "High")
def test_flags_broken_catalog(catalog_shots, step):
    with step("Take a screenshot of the broken catalog (live)", "Capturer le catalogue défectueux (en direct)"):
        step.image(catalog_shots["problem_user"])
    with step("Ask the visual QA assistant to check it", "Demander à l’assistant de contrôle visuel de le vérifier"):
        r, raw = vision.check_catalog(catalog_shots["problem_user"], RELEASE, step=step)
        step.data(answer=raw)
        assert r is not None, "The reply is not valid data"
        assert r.verdict == "fail" and r.distinct_photos <= 2, f"Missed the defect: verdict {r.verdict}, {r.distinct_photos} distinct photo(s) reported"


@catalog
@req("VIS-02", "A healthy catalog is not flagged", "Un catalogue sain n’est pas signalé",
     "On the healthy catalog, the assistant passes the page and counts 6 distinct photos.",
     "Sur le catalogue sain, l’assistant valide la page et compte 6 photos distinctes.", "Medium")
def test_passes_healthy_catalog(catalog_shots, step):
    with step("Take a screenshot of the healthy catalog (live)", "Capturer le catalogue sain (en direct)"):
        step.image(catalog_shots["standard_user"])
    with step("Ask the visual QA assistant to check it", "Demander à l’assistant de contrôle visuel de le vérifier"):
        r, raw = vision.check_catalog(catalog_shots["standard_user"], RELEASE, step=step)
        step.data(answer=raw)
        assert r is not None, "The reply is not valid data"
        assert r.verdict == "pass" and r.distinct_photos == 6, f"False alarm: verdict {r.verdict}, {r.distinct_photos} distinct photo(s), issues: {r.issues[:2]}"


def mod(sample):
    rid = {"M1": "MOD-01", "M2": "MOD-02", "M3": "MOD-03", "M4": "MOD-04"}[sample["id"]]
    want = "allowed" if sample["allowed"] else f"rejected ({sample['category']})"
    fr = "accepté" if sample["allowed"] else f"refusé ({sample['category']})"
    return pytest.param(sample, id=rid, marks=pytest.mark.requirement(
        id=rid, en=f"Moderation: {sample['label']}", fr=f"Modération : {sample['label']}",
        expected_en=f"The photo is {want}.", expected_fr=f"La photo est {fr}.", severity="Critical" if not sample["allowed"] else "Medium"))


@pytest.mark.parametrize("sample", [mod(s) for s in vision.SAMPLES])
def test_moderation(sample, step):
    png = vision.sample_image(sample["id"])
    with step("Show the listing photo to the moderator", "Montrer la photo de l’annonce au modérateur"):
        step.image(png)
        r, raw = vision.moderate(png, RELEASE, step=step)
        step.data(answer=raw)
        assert r is not None, "The reply is not valid data"
        if sample["allowed"]:
            assert r.allowed, f"False positive: rejected as {r.category} ({'; '.join(r.reasons[:2])})"
        else:
            assert not r.allowed, "The photo was allowed"
            assert r.category == sample["category"], f"Rejected for the wrong reason: {r.category}"


@pytest.fixture(scope="module")
def review(checkout_video):
    return {}


def _review(checkout_video, cache, step):
    if "r" not in cache:
        cache["r"] = vision.describe_video(checkout_video["webm"], RELEASE, step=step)
    return cache["r"]


@pytest.fixture
def video_evidence(checkout_video, live, step):
    sink, key, writer = live
    if writer:
        sink.test_update(key, {"video": sink.upload(f"{key}.webm", checkout_video["webm"], "video/webm")})
    return checkout_video


@video
@req("VID-01", "The error shown in the video is reported exactly", "L’erreur visible dans la vidéo est rapportée exactement",
     "The review quotes the error message the store displayed.", "Le compte rendu cite le message d’erreur affiché par la boutique.", "High")
def test_video_error_message(video_evidence, review, step):
    with step("Record a failing checkout (live) and ask the AI to review the video", "Enregistrer un paiement en échec (en direct) et demander à l’IA d’analyser la vidéo"):
        r, raw = _review(video_evidence, review, step)
        step.data(question=f"Error actually shown: {video_evidence['error']}", answer=raw)
        assert r is not None, "The reply is not valid data"
        assert r.error_message and "last name is required" in r.error_message.lower(), f"Reported error: {r.error_message!r}"


@video
@req("VID-02", "Key moments are timed correctly", "Les moments clés sont correctement horodatés",
     f"Sign-in, add to cart, checkout form and error are each reported within ±{TOLERANCE} s of when they happened.",
     f"Connexion, ajout au panier, formulaire de paiement et erreur sont chacun situés à ±{TOLERANCE} s près.", "Medium")
def test_video_timeline(video_evidence, review, step):
    with step("Compare the AI's timeline with the times measured by the test", "Comparer la chronologie de l’IA avec les temps mesurés par le test"):
        r, raw = _review(video_evidence, review, step)
        assert r is not None, "The reply is not valid data"
        rows, bad = [], []
        for event, t in video_evidence["at"].items():
            got = next((e.seconds for e in r.events if e.event == event), None)
            ok = got is not None and abs(got - t) <= TOLERANCE
            if not ok:
                bad.append(len(rows))
            rows.append([event, f"{t:.1f} s", "—" if got is None else f"{got:.1f} s"])
        step.data(table={"cols": ["Event", "Measured", "Reported by the AI"], "rows": rows, "bad": bad})
        assert not bad, f"{len(bad)} event(s) missing or more than {TOLERANCE} s off"


@video
@req("VID-03", "It does not claim an outcome it did not see", "Il n’affirme pas un résultat qu’il n’a pas vu",
     "The review does not say the order was completed (it was not).", "Le compte rendu n’affirme pas que la commande a abouti (ce n’est pas le cas).", "Critical")
def test_video_no_invented_outcome(video_evidence, review, step):
    with step("Check the reported outcome", "Vérifier le résultat rapporté"):
        r, raw = _review(video_evidence, review, step)
        assert r is not None, "The reply is not valid data"
        step.data(answer=r.summary)
        claimed = r.order_completed or any(e.event == "order-completed" for e in r.events)
        assert not claimed, "The review claims the order was completed"
