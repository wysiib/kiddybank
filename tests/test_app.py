from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import ledger, main
from app.models import Transaction

D0 = date(2026, 1, 1)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_URL", f"sqlite:///{tmp_path}/app.db")
    main._engine.cache_clear()
    clock = {"today": D0}
    main.app.dependency_overrides[main.get_today] = lambda: clock["today"]
    c = TestClient(main.app, follow_redirects=False)
    c.clock = clock
    yield c
    main.app.dependency_overrides.clear()
    main._engine.cache_clear()


def login(c, uid, pin):
    c.post("/logout")
    return c.post(f"/login/{uid}", data={"pin": pin})


def account_id(user_id, type):
    with Session(main._engine()) as s:
        return ledger.get_account(s, user_id, type).id


@pytest.fixture
def family(client):
    """Parent (id 1) and Mia (id 2) with 10 EUR, set up through the real routes."""
    assert client.get("/").headers["location"] == "/setup"
    assert client.post("/setup", data={"name": "Mama", "pin": "1234"}).headers["location"] == "/eltern"
    client.post("/eltern/kinder", data={"name": "Mia", "pin": "1111", "avatar": "🦊", "festgeld": "on"})
    client.post("/eltern/buchen", data={"account_id": account_id(2, "giro"), "amount": "10,00"})
    return client


def test_login_pin_and_roles(family):
    r = login(family, 2, "0000")
    assert r.status_code == 200 and "Oh nein" in r.text  # wrong PIN stays on the keypad

    assert login(family, 2, "1111").headers["location"] == "/"
    home = family.get("/home")
    assert home.status_code == 200 and "10,00 €" in home.text
    assert family.get("/eltern").status_code == 403  # kids never see the parent area

    family.post("/logout")
    assert family.get("/home").headers["location"] == "/login"


def test_transfer_confirm_and_insufficient(family):
    login(family, 2, "1111")
    form = {"to_id": account_id(1, "giro")}

    r = family.post("/ueberweisen/pruefen", data={**form, "cents": 9999})
    assert r.status_code == 400 and "nicht genug Geld" in r.text

    r = family.post("/ueberweisen/pruefen", data={**form, "cents": 300})
    assert "Vorher 10,00 €" in r.text and "Nachher 7,00 €" in r.text
    assert "Geschafft" in family.post("/ueberweisen", data={**form, "cents": 300}).text
    with Session(main._engine()) as s:
        assert ledger.get_account(s, 1, "giro").balance_cents == 300

    own = {"to_id": account_id(2, "giro"), "cents": 100}
    assert family.post("/ueberweisen", data=own).status_code == 403  # can't "transfer" to yourself


def test_festgeld_flow_and_module_switch(family):
    login(family, 2, "1111")
    family.post("/festgeld/oeffnen", data={"cents": 500, "product_id": 1})
    page = family.get("/festgeld").text
    assert "Noch 7 Tage" in page and "Abholen" not in page

    family.clock["today"] = D0 + timedelta(days=7)
    page = family.get("/festgeld").text
    assert "Fertig!" in page
    fg_id = account_id(2, "festgeld")
    assert "abgeholt" in family.post(f"/festgeld/{fg_id}/abholen").text

    login(family, 1, "1234")  # parent switches the module off for Mia
    family.post("/eltern/kinder/2/module", data={})
    login(family, 2, "1111")
    assert family.post("/festgeld/oeffnen", data={"cents": 100, "product_id": 1}).status_code == 403
    assert "/festgeld" not in family.get("/home").text  # tile is gone


def test_interest_celebration_shows_until_seen(family):
    with Session(main._engine()) as s:
        ledger.get_account(s, 2, "giro").balance_cents = 10_000
        s.commit()
    login(family, 2, "1111")
    family.clock["today"] = D0 + timedelta(days=30)
    assert "Zinsen bekommen" in family.get("/home").text
    assert "Zinsen bekommen" in family.get("/home").text  # not marked seen by merely looking
    family.post("/gesehen")
    assert "Zinsen bekommen" not in family.get("/home").text


def test_parent_configures_rates_and_kid_opens_two_deposits(family):
    login(family, 1, "1234")
    family.post("/eltern/produkte", data={"name": "Turbo", "days": 3, "rate": "50"})
    assert "Turbo" in family.get("/eltern").text
    assert family.post("/eltern/produkte/4/loeschen").status_code == 303
    assert "Turbo" not in family.get("/eltern").text
    family.post("/eltern/produkte", data={"name": "Turbo", "days": 3, "rate": "50"})  # re-add (SQLite reuses id 4)
    assert family.post("/eltern/produkte", data={"name": "Kaputt", "days": 3, "rate": "500"}).status_code == 400
    assert family.post("/eltern/kinder/2/module", data={"rate": "500", "festgeld": "on"}).status_code == 400
    family.post("/eltern/kinder/2/module", data={"rate": "3,5", "festgeld": "on"})
    assert "3,5" in family.get("/eltern").text

    login(family, 2, "1111")
    home = family.get("/home").text
    assert "Zinsen im Jahr" not in home and "bekommst du etwa" not in home  # empty balance: nothing to promise
    family.post("/festgeld/oeffnen", data={"cents": 300, "product_id": 1})
    family.post("/festgeld/oeffnen", data={"cents": 400, "product_id": 4})  # the new "Turbo" product
    page = family.get("/festgeld").text
    assert "2 Cent" in page  # bars start from the 10 EUR demo amount: Kurz pays 2 Cent, this kid's Giro 0
    bars = family.get("/festgeld/vorschau", params={"cents": 10_000, "product_id": 1}).text  # 100 EUR
    assert 'id="towers-1"' in bars and "23 Cent" in bars and "6 Cent" in bars  # Kurz 12 % for 7 days vs 3,5 % Giro
    assert 'id="towers-4"' in bars and "41 Cent" in bars  # Turbo 50 % for 3 days, and every tile is refreshed
    assert "Kurz" in page and "Turbo" in page and page.count("Noch 7 Tage") == 1 and "Noch 3 Tage" in page

    def offers():  # what the kid can pick from, i.e. the part of the page after the "new deposit" heading
        return family.get("/festgeld").text.split("Neue Schatztruhe")[1]

    assert "Turbo" in offers()
    login(family, 1, "1234")  # deleting hides it from kids; the open deposit is unaffected
    family.post("/eltern/produkte/4/loeschen")
    login(family, 2, "1111")
    assert "Turbo" not in offers() and "Kurz" in offers()
    assert "Turbo" in family.get("/festgeld").text  # still shown on the deposit card


def test_parent_changes_avatar(family):
    family.post("/eltern/kinder/2/module", data={"avatar": "🐼"})
    with Session(main._engine()) as s:
        assert s.get(main.User, 2).avatar == "🐼"
    family.post("/eltern/kinder/2/module", data={"avatar": "not-an-avatar"})  # unknown values are ignored
    with Session(main._engine()) as s:
        assert s.get(main.User, 2).avatar == "🐼"


def test_dauerauftrag_uses_chosen_weekday(family):
    family.post("/eltern/dauerauftrag", data={"kid_id": 2, "amount": "2,00", "interval": "weekly", "weekday": 4})
    with Session(main._engine()) as s:
        r = s.exec(select(main.RecurringRule)).one()
        assert r.next_run.weekday() == 4 and r.next_run > family.clock["today"]


JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def add_goal(c, **kw):
    return c.post("/ziele", data={"name": "Lego", "emoji": "🧸", "cents": 2000, **kw})


def test_goal_create_photo_and_progress(family):
    login(family, 2, "1111")
    assert add_goal(family, name="").status_code == 400  # neither name nor photo
    r = family.post("/ziele", data={"name": "", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 303
    page = family.get("/ziele").text
    assert "/ziele/1/bild" in page and "Noch 10,00 € bis dahin" in page  # 10 EUR of 20 EUR
    img = family.get("/ziele/1/bild")
    assert img.content == JPEG and img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert img.headers["cache-control"] == "private, no-cache"

    assert add_goal(family).status_code == 303  # name only works too
    assert "Lego" in family.get("/ziele").text


def test_goal_empty_photo_part(family):
    # browsers send an empty "photo" part when nothing was picked
    login(family, 2, "1111")
    r = family.post("/ziele", data={"name": "Ball", "cents": 500}, files={"photo": ("", b"", "application/octet-stream")})
    assert r.status_code == 303
    page = family.get("/ziele").text
    assert "Ball" in page and "/bild" not in page


def test_goal_rejects_bad_input(family):
    login(family, 2, "1111")
    r = family.post("/ziele", data={"name": "x", "cents": 500}, files={"photo": ("g.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 400 and "geht es leider nicht" in r.text
    big = JPEG + b"x" * ledger.MAX_PHOTO_BYTES
    r = family.post("/ziele", data={"name": "x", "cents": 500}, files={"photo": ("g.jpg", big, "image/jpeg")})
    assert r.status_code == 400 and "zu groß" in r.text
    assert add_goal(family, cents=0).status_code == 400

    for _ in range(3):
        assert add_goal(family).status_code == 303
    r = add_goal(family)
    assert r.status_code == 400 and "schon 3" in r.text


def test_goal_scoping_between_kids_and_parent(family):
    login(family, 2, "1111")
    family.post("/ziele", data={"name": "Lego", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})

    login(family, 1, "1234")
    family.post("/eltern/kinder", data={"name": "Tom", "pin": "2222"})
    assert family.get("/ziele/1/bild").status_code == 200  # a parent may see it

    login(family, 3, "2222")
    assert family.get("/ziele/1/bild").status_code == 404
    assert family.post("/ziele/1/loeschen").status_code == 404
    assert family.post("/ziele/1/geschafft").status_code == 404
    assert "Lego" not in family.get("/ziele").text


def test_goal_finish_and_delete(family):
    login(family, 2, "1111")
    add_goal(family, cents=1500)  # goal 1: 10 EUR of 15 EUR
    add_goal(family, name="Ball", cents=500)  # goal 2: already affordable

    r = family.post("/ziele/1/geschafft")
    assert r.status_code == 400 and "nicht genug Geld" in r.text  # not reached yet

    r = family.post("/ziele/2/geschafft")
    assert r.status_code == 200 and "Mama oder Papa" in r.text
    page = family.get("/ziele").text
    assert "✅ Geschafft" in page and "Ball" in page
    assert family.post("/ziele/2/loeschen").status_code == 404  # finished goals stay as a record

    assert family.post("/ziele/1/loeschen").status_code == 303
    assert "Lego" not in family.get("/ziele").text


def test_goal_home_card_and_celebration_once(family):
    login(family, 2, "1111")
    assert "🎯" in family.get("/home").text and "/ziele" in family.get("/home").text  # tile before any goal
    add_goal(family, cents=1500)  # 10 EUR of 15 EUR
    home = family.get("/home").text
    assert "Lego" in home and "dein Ziel erreicht" not in home

    login(family, 1, "1234")
    family.post("/eltern/buchen", data={"account_id": account_id(2, "giro"), "amount": "5,00"})
    login(family, 2, "1111")
    assert "dein Ziel erreicht" in family.get("/home").text
    assert "dein Ziel erreicht" in family.get("/home").text  # looking is not enough
    family.post("/gesehen")
    assert "dein Ziel erreicht" not in family.get("/home").text
    assert "Ziel geschafft" in family.get("/ziele").text  # still reachable, button waits


def test_parent_sees_kids_goals(family):
    login(family, 2, "1111")
    family.post("/ziele", data={"name": "Lego", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})
    login(family, 1, "1234")
    page = family.get("/eltern").text
    assert "Lego" in page and "/ziele/1/bild" in page and "20,00 €" in page


def test_parent_sees_kid_statement(family):
    login(family, 1, "1234")
    page = family.get("/eltern/kinder/2/konto")
    assert page.status_code == 200
    assert "Mia" in page.text and "Eltern haben Geld eingezahlt" in page.text and "10,00 €" in page.text
    assert 'href="/eltern"' in page.text
    assert family.get("/eltern/kinder/1/konto").status_code == 404  # a parent, not a kid
    assert family.get("/eltern/kinder/99/konto").status_code == 404
    login(family, 2, "1111")
    assert family.get("/eltern/kinder/2/konto").status_code == 403


def test_parent_catch_up_keeps_kid_celebrations(family):
    family.post("/eltern/dauerauftrag", data={"kid_id": 2, "amount": "2,00", "interval": "weekly", "weekday": 4})
    family.clock["today"] = D0 + timedelta(days=30)
    login(family, 1, "1234")
    assert "Taschengeld" in family.get("/eltern/kinder/2/konto").text  # the parent's visit booked it
    login(family, 2, "1111")
    home = family.get("/home").text
    assert "Zinsen bekommen" in home and "Taschengeld" in home  # still unseen, kid gets the celebration
    family.post("/gesehen")
    home = family.get("/home").text
    assert "Zinsen bekommen" not in home


def test_home_week_card(family):
    login(family, 2, "1111")
    home = family.get("/home").text
    assert "Deine Woche" in home and "Von anderen" in home and "+10,00 €" in home and "Ausgegeben" not in home
    family.post("/ueberweisen", data={"to_id": account_id(1, "giro"), "cents": 300})
    assert "-3,00 €" in family.get("/home").text
    family.clock["today"] = D0 + timedelta(days=400)  # the manual bookings are old news, only interest is left
    later = family.get("/home").text
    assert "Zinsen" in later and "Von anderen" not in later and "Ausgegeben" not in later
    with Session(main._engine()) as s:
        for tx in s.exec(select(Transaction)).all():
            s.delete(tx)
        s.commit()
    assert "Deine Woche" not in family.get("/home").text  # empty week: no card


def test_edit_dauerauftrag(family):
    family.post("/eltern/dauerauftrag", data={"kid_id": 2, "amount": "2,00", "interval": "weekly", "weekday": 4})
    family.post("/eltern/dauerauftrag/1", data={"amount": "3,50", "interval": "monthly", "weekday": 4, "monthday": 15})
    with Session(main._engine()) as s:
        r = s.exec(select(main.RecurringRule)).one()
        assert (r.amount_cents, r.interval, r.next_run.day) == (350, "monthly", 15)
    assert 'value="3,50"' in family.get("/eltern").text
