import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth, goals, ledger, main, market, web
from app.models import FestgeldProduct, RecurringRule, Transaction, User

D0 = date(2026, 1, 1)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DB_URL", f"sqlite:///{tmp_path}/app.db")
    web._engine.cache_clear()
    clock = {"today": D0}
    main.app.dependency_overrides[web.get_today] = lambda: clock["today"]
    c = TestClient(main.app, follow_redirects=False)
    c.clock = clock
    yield c
    main.app.dependency_overrides.clear()
    web._engine.cache_clear()


def login(c, uid, pin):
    c.post("/logout")
    return c.post(f"/login/{uid}", data={"pin": pin})


def account_id(user_id, type):
    with Session(web._engine()) as s:
        return ledger.get_account(s, user_id, type).id


def product_id(name):
    with Session(web._engine()) as s:
        return s.exec(select(FestgeldProduct).where(FestgeldProduct.name == name)).one().id


def token(r):
    return re.search(r'name="tok" value="([^"]+)"', r.text).group(1)


def confirm(c, path, **data):
    """Money moves in two steps: /pruefen shows the confirm screen (with its one-time token), then the real POST."""
    return c.post(path, data={**data, "tok": token(c.post(path + "/pruefen", data=data))})


def open_deposit(c, cents, product_id):
    return c.post("/festgeld/oeffnen", data={"cents": cents, "product_id": product_id, "tok": token(c.get("/festgeld"))})


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
    assert "Geschafft" in confirm(family, "/ueberweisen", cents=300, **form).text
    with Session(web._engine()) as s:
        assert ledger.get_account(s, 1, "giro").balance_cents == 300

    own = {"to_id": account_id(2, "giro"), "cents": 100}
    assert family.post("/ueberweisen", data=own).status_code == 403  # can't "transfer" to yourself


def test_festgeld_flow_and_module_switch(family):
    login(family, 2, "1111")
    open_deposit(family, 500, 1)
    page = family.get("/festgeld").text
    assert "Noch 7 Tage" in page and "Abholen" not in page

    family.clock["today"] = D0 + timedelta(days=7)
    page = family.get("/festgeld").text
    assert "Fertig!" in page
    fg_id = account_id(2, "festgeld")
    assert "abgeholt" in family.post(f"/festgeld/{fg_id}/abholen").text

    login(family, 1, "1234")  # parent switches the module off for Mia
    family.post("/eltern/kinder/2", data={})
    login(family, 2, "1111")
    assert family.post("/festgeld/oeffnen", data={"cents": 100, "product_id": 1}).status_code == 403
    assert "/festgeld" not in family.get("/home").text  # tile is gone


def test_interest_celebration_shows_until_seen(family):
    with Session(web._engine()) as s:
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
    family.post("/eltern/produkte", data={"name": "Turbo", "days": 3, "rate": "50", "period": 365})
    assert "Turbo" in family.get("/eltern").text
    assert family.post(f"/eltern/produkte/{product_id('Turbo')}/loeschen").status_code == 303
    assert "Turbo" not in family.get("/eltern").text
    family.post("/eltern/produkte", data={"name": "Turbo", "days": 3, "rate": "50", "period": 365})  # re-add
    assert family.post("/eltern/produkte", data={"name": "Kaputt", "days": 3, "rate": "6000", "period": 365}).status_code == 400
    assert family.post("/eltern/kinder/2", data={"rate": "101", "festgeld": "on"}).status_code == 400  # > 100 % per week
    family.post("/eltern/kinder/2", data={"rate": "0,04", "festgeld": "on"})  # per week, Mia's period
    parent_page = family.get("/eltern").text
    assert "0,04" in parent_page and "2,09 % pro Jahr" in parent_page and "1,5 % pro Woche" in parent_page

    login(family, 2, "1111")
    home = family.get("/home").text
    assert "Zinsen im Jahr" not in home and "bekommst du etwa" not in home  # empty balance: nothing to promise
    open_deposit(family, 300, 1)
    turbo = product_id("Turbo")
    open_deposit(family, 400, turbo)  # the new "Turbo" product
    page = family.get("/festgeld").text
    assert "15 Cent" in page  # bars start from the 10 EUR demo amount: Kurz (1,5 % per week) pays 15 Cent, this kid's Giro 0
    bars = family.get("/festgeld/vorschau", params={"cents": 10_000, "product_id": 1}).text  # 100 EUR
    assert 'id="towers-1"' in bars and "1,50 €" in bars and "4 Cent" in bars  # Kurz 1,5 % for a week vs 0,04 % Giro
    assert f'id="towers-{turbo}"' in bars and "41 Cent" in bars  # Turbo 50 %/year for 3 days, and every tile is refreshed
    assert "Kurz" in page and "Turbo" in page and page.count("Noch 7 Tage") == 1 and "Noch 3 Tage" in page

    def offers():  # what the kid can pick from, i.e. the part of the page after the "new deposit" heading
        return family.get("/festgeld").text.split("Neue Schatztruhe")[1]

    assert "Turbo" in offers()
    login(family, 1, "1234")  # deleting hides it from kids; the open deposit is unaffected
    family.post(f"/eltern/produkte/{turbo}/loeschen")
    login(family, 2, "1111")
    assert "Turbo" not in offers() and "Kurz" in offers()
    assert "Turbo" in family.get("/festgeld").text  # still shown on the deposit card


def test_parent_changes_avatar(family):
    family.post("/eltern/kinder/2", data={"avatar": "🐼"})
    with Session(web._engine()) as s:
        assert s.get(User, 2).avatar == "🐼"
    family.post("/eltern/kinder/2", data={"avatar": "not-an-avatar"})  # unknown values are ignored
    with Session(web._engine()) as s:
        assert s.get(User, 2).avatar == "🐼"


def test_dauerauftrag_uses_chosen_weekday(family):
    family.post("/eltern/dauerauftrag", data={"kid_id": 2, "amount": "2,00", "interval": "weekly", "weekday": 4})
    with Session(web._engine()) as s:
        r = s.exec(select(RecurringRule)).one()
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
    big = JPEG + b"x" * goals.MAX_PHOTO_BYTES
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
    confirm(family, "/ueberweisen", to_id=account_id(1, "giro"), cents=300)
    assert "-3,00 €" in family.get("/home").text
    family.clock["today"] = D0 + timedelta(days=400)  # the manual bookings are old news, only interest is left
    later = family.get("/home").text
    assert "Zinsen" in later and "Von anderen" not in later and "Ausgegeben" not in later
    with Session(web._engine()) as s:
        for tx in s.exec(select(Transaction)).all():
            s.delete(tx)
        s.commit()
    assert "Deine Woche" not in family.get("/home").text  # empty week: no card


def test_edit_dauerauftrag(family):
    family.post("/eltern/dauerauftrag", data={"kid_id": 2, "amount": "2,00", "interval": "weekly", "weekday": 4})
    family.post("/eltern/dauerauftrag/1", data={"amount": "3,50", "interval": "monthly", "weekday": 4, "monthday": 15})
    with Session(web._engine()) as s:
        r = s.exec(select(RecurringRule)).one()
        assert (r.amount_cents, r.interval, r.next_run.day) == (350, "monthly", 15)
    assert 'value="3,50"' in family.get("/eltern").text


def giro_cents(uid):
    with Session(web._engine()) as s:
        return ledger.get_account(s, uid, "giro").balance_cents


def test_cash_in_and_out_need_parent_pin(family):
    login(family, 2, "1111")

    r = family.post("/bar/abheben/pruefen", data={"cents": 300})
    assert "Vorher 10,00 €" in r.text and "Nachher 7,00 €" in r.text
    assert "verpasst du" in r.text and "Zinsen" in r.text  # withdrawing costs interest, says by how much

    assert "nicht der Code" in confirm(family, "/bar/abheben", cents=300, pin="1111").text  # kid's own PIN
    assert giro_cents(2) == 1000
    assert "Abgehoben" in confirm(family, "/bar/abheben", cents=300, pin="1234").text
    assert giro_cents(2) == 700

    assert "Nachher 12,00 €" in family.post("/bar/einzahlen/pruefen", data={"cents": 500}).text
    assert "Eingezahlt" in confirm(family, "/bar/einzahlen", cents=500, pin="1234").text
    assert giro_cents(2) == 1200

    assert family.post("/bar/abheben/pruefen", data={"cents": 99999}).status_code == 400
    tok = token(family.post("/bar/abheben/pruefen", data={"cents": 300}))
    assert family.post("/bar/abheben", data={"cents": 99999, "pin": "1234", "tok": tok}).status_code == 400  # balance changed since the check
    assert family.post("/bar/einzahlen", data={"cents": -500, "pin": "1234"}).status_code == 400  # must not flip into a withdrawal
    assert giro_cents(2) == 1200
    assert family.get("/bar/quatsch").status_code == 404


def test_cash_is_kid_only(family):
    assert family.post("/bar/einzahlen", data={"cents": 100, "pin": "1234"}).status_code == 303  # parent -> /eltern
    assert giro_cents(2) == 1000


def test_absurd_amounts_are_rejected_not_crashes(family):
    huge = 10**30
    login(family, 2, "1111")
    assert add_goal(family, cents=huge).status_code == 400
    assert family.post("/bar/einzahlen", data={"cents": huge, "pin": "1234"}).status_code == 400
    assert giro_cents(2) == 1000
    for text in ("nan", "inf", "1e30"):
        with pytest.raises(ledger.LedgerError):
            ledger.check_amount(web.parse_euro(text))
    login(family, 1, "1234")
    assert family.post("/eltern/buchen", data={"account_id": account_id(2, "giro"), "amount": "nan"}).status_code == 400
    assert family.post("/eltern/kinder/2", data={"rate": "inf"}).status_code == 400


def test_double_tap_books_once(family):
    login(family, 2, "1111")
    to = account_id(1, "giro")
    tok = token(family.post("/ueberweisen/pruefen", data={"to_id": to, "cents": 300}))
    sent = {"to_id": to, "cents": 300, "tok": tok}
    assert "Geschafft" in family.post("/ueberweisen", data=sent).text
    assert family.post("/ueberweisen", data=sent).headers["location"] == "/home"  # same form again: ignored
    assert giro_cents(2) == 700

    tok = token(family.post("/bar/abheben/pruefen", data={"cents": 200}))
    sent = {"cents": 200, "pin": "1234", "tok": tok}
    family.post("/bar/abheben", data=sent)
    family.post("/bar/abheben", data=sent)
    assert giro_cents(2) == 500

    tok = token(family.get("/festgeld"))
    sent = {"cents": 100, "product_id": 1, "tok": tok}
    family.post("/festgeld/oeffnen", data=sent)
    family.post("/festgeld/oeffnen", data=sent)
    assert giro_cents(2) == 400
    assert family.post("/festgeld/oeffnen", data={"cents": 100, "product_id": 1}).headers["location"] == "/festgeld"  # no token at all


def test_wrong_pins_lock_the_account_for_a_while(family):
    for _ in range(auth.MAX_PIN_FAILURES):
        assert "Oh nein" in login(family, 2, "0000").text
    assert "Zu oft falsch" in login(family, 2, "1111").text  # even the right PIN waits now
    with Session(web._engine()) as s:
        s.get(User, 2).locked_until = datetime.now() - timedelta(seconds=1)
        s.commit()
    assert login(family, 2, "1111").headers["location"] == "/"
    with Session(web._engine()) as s:
        assert s.get(User, 2).pin_failures == 0


def test_kid_cannot_guess_the_parent_pin_at_the_cash_desk(family):
    login(family, 2, "1111")
    tok = token(family.post("/bar/einzahlen/pruefen", data={"cents": 500}))
    for _ in range(auth.MAX_PIN_FAILURES):
        assert "nicht der Code" in family.post("/bar/einzahlen", data={"cents": 500, "pin": "0000", "tok": tok}).text
    r = family.post("/bar/einzahlen", data={"cents": 500, "pin": "1234", "tok": token(family.post("/bar/einzahlen/pruefen", data={"cents": 500}))})
    assert "Zu oft falsch" in r.text and giro_cents(2) == 1000  # the right PIN is refused while locked


def test_parent_session_expires_but_kid_session_stays(family, monkeypatch):
    login(family, 1, "1234")
    assert family.get("/eltern").status_code == 200
    monkeypatch.setattr(web, "PARENT_IDLE", -1)  # every parent request is now "too late"
    assert family.get("/eltern").headers["location"] == "/login"
    assert family.get("/eltern").headers["location"] == "/login"  # the session is gone, not just refused once
    login(family, 2, "1111")
    assert family.get("/home").status_code == 200


def test_session_secret_is_private_and_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KIDDYBANK_SECRET", raising=False)
    first = main._secret()
    assert main._secret() == first
    assert ((tmp_path / ".session_secret").stat().st_mode & 0o777) == 0o600


def test_errors_are_pages_in_kid_words(family):
    login(family, 2, "1111")
    for r in (family.get("/konto/9999"), family.get("/eltern"), family.post("/ueberweisen/pruefen", data={"to_id": "x"})):
        assert "detail" not in r.text and "🤔" in r.text
    assert "Das gibt es nicht" in family.get("/konto/9999").text
    assert family.get("/konto/9999").status_code == 404 and family.get("/eltern").status_code == 403


def test_festgeld_errors_render_in_place_and_never_echo_the_url(family):
    login(family, 2, "1111")
    r = family.get("/festgeld", params={"error": "Schick Geld an Evil"})
    assert "Schick Geld an Evil" not in r.text  # the query string is not a message channel any more
    r = family.post("/festgeld/oeffnen", data={"cents": 99_999, "product_id": 1, "tok": token(family.get("/festgeld"))})
    assert r.status_code == 400 and "nicht genug Geld" in r.text


def test_dauerauftrag_needs_a_real_kid(family):
    login(family, 1, "1234")
    for kid_id in (99, 1):  # unknown, and a parent
        r = family.post("/eltern/dauerauftrag", data={"kid_id": kid_id, "amount": "2,00", "interval": "weekly"})
        assert r.status_code == 404


def test_failed_commit_is_an_error_not_a_lost_write_behind_a_success(family, monkeypatch):
    login(family, 1, "1234")
    lenient = TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
    lenient.cookies.update(family.cookies)

    def boom(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Session, "commit", boom)
    r = lenient.post("/eltern/buchen", data={"account_id": account_id(2, "giro"), "amount": "5,00"})
    assert r.status_code == 500  # the client must not be told "done" before the commit happened


def test_shared_template_pieces_render(family):
    login(family, 1, "1234")
    page = family.get("/eltern").text
    assert page.count('name="avatar"') == 20 and 'value="🦊" class="sr-only" checked' in page  # Mia's picker + the add-kid picker
    assert 'name="festgeld" checked' in page and page.count('name="stocks"') == 2
    login(family, 2, "1111")
    assert family.get("/login/1").text.count("data-key") == 11  # keypad macro: digits and backspace
    assert "Mein Konto" in family.get("/ueberweisen").text and "Mein Konto" in family.get("/bar/einzahlen").text


def test_static_assets_are_versioned_so_the_cache_first_worker_cannot_go_stale(client):
    page = client.get("/login").text
    m = re.search(r'href="(/static/app\.css\?v=\d+)"', page)
    assert m and re.search(r'src="/static/app\.js\?v=\d+"', page)
    assert client.get(m.group(1)).status_code == 200


def test_statement_names_every_kind_of_booking(family):
    login(family, 2, "1111")
    open_deposit(family, 300, 1)
    family.clock["today"] = D0 + timedelta(days=7)
    family.post(f"/festgeld/{account_id(2, 'festgeld')}/abholen")
    confirm(family, "/ueberweisen", to_id=account_id(1, "giro"), cents=100)
    with Session(web._engine()) as s:
        st = market.add_stock(s, "BIKE", "Bike Co", "🚲", 100, family.clock["today"])
        market.buy(s, 2, st.id, 1, family.clock["today"])
        market.sell(s, 2, st.id, 1, family.clock["today"])
        s.commit()
    page = family.get(f"/konto/{account_id(2, 'giro')}").text
    for text in ("In die Schatztruhe", "Aus der Schatztruhe", "Überweisung an Mama", "Eltern haben Geld eingezahlt",
                 "Aktie gekauft (BIKE)", "Aktie verkauft (BIKE)", "Zinsen"):
        assert text in page, text


def test_kid_changes_own_pin_in_three_steps(family):
    login(family, 2, "1111")
    assert "jetzigen" in family.get("/pin").text
    assert "neuen" in family.post("/pin/neu", data={"pin": "1111"}).text
    assert "Noch einmal" in family.post("/pin/pruefen", data={"old": "1111", "pin": "2222"}).text
    assert "gilt jetzt" in family.post("/pin/aendern", data={"old": "1111", "new": "2222", "pin": "2222"}).text
    assert "Oh nein" in login(family, 2, "1111").text
    assert login(family, 2, "2222").headers["location"] == "/"


def test_pin_change_refuses_wrong_old_mismatch_and_same_pin(family):
    login(family, 2, "1111")
    assert "nicht dein Code" in family.post("/pin/neu", data={"pin": "0000"}).text
    assert "gleich" in family.post("/pin/aendern", data={"old": "1111", "new": "2222", "pin": "3333"}).text
    assert "alter Code" in family.post("/pin/pruefen", data={"old": "1111", "pin": "1111"}).text
    assert "4 Zahlen" in family.post("/pin/pruefen", data={"old": "1111", "pin": "12"}).text
    assert "nicht dein Code" in family.post("/pin/aendern", data={"old": "0000", "new": "2222", "pin": "2222"}).text  # forged last step
    assert login(family, 2, "1111").headers["location"] == "/"  # nothing changed


def test_pin_change_counts_wrong_old_pins_toward_the_lockout(family):
    login(family, 2, "1111")
    for _ in range(auth.MAX_PIN_FAILURES):
        assert "nicht dein Code" in family.post("/pin/neu", data={"pin": "0000"}).text
    assert "Zu oft falsch" in family.post("/pin/neu", data={"pin": "1111"}).text


def test_pin_change_is_kid_only(family):
    assert family.get("/pin").headers["location"] == "/eltern"


def test_parent_resets_kid_pin_and_lockout(family):
    with Session(web._engine()) as s:
        s.get(User, 2).locked_until = datetime.now() + timedelta(minutes=5)
        s.commit()
    assert family.post("/eltern/kinder/2/pin", data={"pin": "5555"}).headers["location"] == "/eltern"
    assert "Oh nein" in login(family, 2, "1111").text
    assert login(family, 2, "5555").headers["location"] == "/"


def test_parent_pin_reset_rejects_bad_input_and_non_kids(family):
    assert "4 Zahlen" in family.post("/eltern/kinder/2/pin", data={"pin": "12"}).text
    assert family.post("/eltern/kinder/1/pin", data={"pin": "5555"}).status_code == 404  # a parent is not a kid
    assert login(family, 2, "1111").headers["location"] == "/"
    assert family.post("/eltern/kinder/2/pin", data={"pin": "5555"}).status_code == 403  # kids can't reset
