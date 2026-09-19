from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import ledger, main

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
    assert family.post("/eltern/produkte", data={"name": "Kaputt", "days": 3, "rate": "500"}).status_code == 400
    family.post("/eltern/kinder/2/zins", data={"rate": "3,5"})
    assert "3,5" in family.get("/eltern").text

    login(family, 2, "1111")
    assert "3,5 %" in family.get("/home").text
    family.post("/festgeld/oeffnen", data={"cents": 300, "product_id": 1})
    family.post("/festgeld/oeffnen", data={"cents": 400, "product_id": 4})  # the new "Turbo" product
    page = family.get("/festgeld").text
    assert "Kurz" in page and "Turbo" in page and page.count("Noch 7 Tage") == 1 and "Noch 3 Tage" in page

    def offers():  # what the kid can pick from, i.e. the part of the page after the "new deposit" heading
        return family.get("/festgeld").text.split("Neue Schatztruhe")[1]

    assert "Turbo" in offers()
    login(family, 1, "1234")  # deactivating hides it from kids; the open deposit is unaffected
    family.post("/eltern/produkte/4", data={"name": "Turbo", "days": 3, "rate": "50"})
    login(family, 2, "1111")
    assert "Turbo" not in offers() and "Kurz" in offers()
    assert "Turbo" in family.get("/festgeld").text  # still shown on the deposit card
