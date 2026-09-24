"""All user-facing text lives here. Add a locale by adding a dict to STRINGS; call sites never change."""

from datetime import date

LOCALE = "de"

STRINGS = {
    "de": {
        "app.title": "Kinderbank",
        "app.logout": "Tschüss",
        "app.back": "Zurück",
        "app.ok": "Toll!",
        "app.next": "Weiter",
        "app.save": "Speichern",
        "app.confirm.yes": "Ja, schicken!",

        # login
        "login.who": "Wer bist du?",
        "login.pin": "Dein Geheim-Code",
        "login.pin.wrong": "Oh nein, das war nicht dein Code. Versuch es noch einmal!",
        "pin.change.old": "Gib deinen jetzigen Geheim-Code ein",
        "pin.change.new": "Denk dir einen neuen Geheim-Code aus",
        "pin.change.again": "Noch einmal, damit wir sicher sind",
        "pin.change.same": "Das ist dein alter Code. Such dir einen neuen aus!",
        "pin.change.mismatch": "Die beiden Codes waren nicht gleich. Versuch es noch einmal!",
        "pin.change.done": "Dein neuer Geheim-Code gilt jetzt!",
        "pin.change.lesson": "Dein Geheim-Code ist wie ein Schlüssel zu deinem Konto. Sag ihn niemandem außer deinen Eltern.",
        "setup.title": "Willkommen bei der Kinderbank!",
        "setup.intro": "Lege zuerst das Eltern-Konto an.",
        "setup.name": "Name",
        "setup.pin": "Eltern-Code (4 Zahlen)",

        # home
        "home.hello": "Hallo {name}!",
        "acct.checking": "Mein Konto",
        "acct.checking.sub": "Girokonto",
        "acct.term_deposit": "Schatztruhe",
        "acct.term_deposit.sub": "Festgeld",
        "home.transfer": "Überweisen",
        "home.interest": "{when} bekommst du etwa {amount} Zinsen.",
        "home.interest.days": "In {days} Tagen",
        "home.interest.tomorrow": "Morgen",
        "week.title": "Deine Woche",
        "week.recurring": "Taschengeld",
        "week.interest": "Zinsen",
        "week.other": "Von anderen",
        "week.spent": "Ausgegeben",
        "week.lesson": "Taschengeld und Zinsen kommen rein. Was du ausgibst, geht raus.",

        # celebrations
        "cel.interest": "Du hast {amount} Zinsen bekommen!",
        "cel.interest.why": "Die Bank bezahlt dich dafür, dass dein Geld bei ihr bleibt. Das nennt man Zinsen.",
        "cel.recurring": "Taschengeld ist da! {amount}",
        "cel.recurring.why": "Dein Taschengeld kommt regelmäßig von allein. Das nennt man Dauerauftrag.",
        "cel.goal": "Du hast dein Ziel erreicht!",
        "cel.goal.why": "Du hast so lange gespart, bis genug Geld da war. Das nennt man ein Sparziel.",

        # statement
        "stmt.title": "Kontoauszug",
        "stmt.empty": "Hier ist noch nichts passiert.",
        "stmt.balance": "Kontostand",
        "tx.interest": "Zinsen",
        "tx.recurring": "Taschengeld (Dauerauftrag)",
        "tx.term_deposit.in": "Aus der Schatztruhe",
        "tx.term_deposit.out": "In die Schatztruhe",
        "tx.transfer.in": "Überweisung von {name}",
        "tx.transfer.out": "Überweisung an {name}",
        "tx.parent.in": "Eltern haben Geld eingezahlt",
        "tx.parent.out": "Eltern haben Geld abgehoben",
        "tx.stock_buy": "Aktie gekauft",
        "tx.stock_sell": "Aktie verkauft",

        # transfer
        "xfer.title": "Überweisen",
        "xfer.to": "An wen?",
        "xfer.amount": "Wie viel?",
        "xfer.reset": "Auf null",
        "xfer.confirm": "Wirklich {amount} an {name} schicken?",
        "xfer.stays": "Bleibt bei dir",
        "xfer.arrives": "Kommt bei {name} an",
        "xfer.lesson": "Bei einer Überweisung geht das Geld von deinem Konto auf das Konto von jemand anderem.",
        "xfer.done": "Geschafft! Das Geld ist angekommen.",

        # cash in / out (real money with a parent, confirmed by the parent's PIN)
        "home.deposit": "Einzahlen",
        "home.withdraw": "Abheben",
        "home.pin": "Geheim-Code ändern",
        "cash.in.title": "Einzahlen",
        "cash.out.title": "Abheben",
        "cash.in.intro": "Du gibst deinen Eltern echtes Geld. Die Bank schreibt es auf dein Konto.",
        "cash.out.intro": "Du bekommst echtes Geld von deinen Eltern. Die Bank zieht es von deinem Konto ab.",
        "cash.in.confirm": "Wirklich {amount} einzahlen?",
        "cash.out.confirm": "Wirklich {amount} abheben?",
        "cash.in.lesson": "Beim Einzahlen gibst du echtes Geld ab und die Bank schreibt es auf deinem Konto gut. Ab jetzt bekommt es Zinsen!",
        "cash.out.lesson": "Beim Abheben bekommst du echtes Geld und die Bank zieht es von deinem Konto ab.",
        "cash.out.interest": "Aber Achtung: Geld auf dem Konto bekommt Zinsen. Wenn du {amount} abhebst, verpasst du {lost} Zinsen {per}.",
        "cash.out.interest.plain": "Aber Achtung: Geld auf dem Konto bekommt Zinsen. Was du abhebst, bringt dir keine Zinsen mehr.",
        "cash.pin": "Eltern: bitte gebt euren Geheim-Code ein",
        "cash.pin.wrong": "Das war nicht der Code von den Eltern.",
        "cash.in.done": "Eingezahlt! Dein Geld liegt jetzt auf dem Konto.",
        "cash.out.done": "Abgehoben! Du hast dein Geld bekommen.",
        "cash.sender": "Von wem / wofür? (optional)",
        "cash.sender.hint": "Wenn du nichts einträgst, steht im Kontoauszug: „{default}“.",

        # festgeld
        "td.title": "Schatztruhe",
        "td.lesson": "Wenn du dein Geld länger warten lässt, bekommst du mehr Zinsen. Dafür kommst du erst später wieder dran. Das nennt man Festgeld.",
        "td.open": "Neue Schatztruhe",
        "td.term": "Wie lange warten?",
        "td.days": "{n} Tage",
        "td.days.1": "1 Tag",
        "td.cent": "{n} Cent",
        "coin.unit": "Große Münze = {amount}",
        "short.have": "Du hast {amount}",
        "short.need": "Du brauchst {amount}",
        "cash.stays": "Bleibt auf dem Konto",
        "cash.leaves": "Geht raus",
        "cash.after": "Danach hast du {amount}",
        "td.yours": "Deins",
        "td.comes": "Kommt dazu",
        "td.extra": "Extra fürs Warten",
        "coin.unit.small": "Kleine Münze = {amount}",
        "grow.title": "Wenn du wartest",
        "grow.lesson": "Wenn du nichts ausgibst und dein Taschengeld sparst, wird dein Geld immer mehr – je länger, desto schneller.",
        "grow.h.7": "1 Woche",
        "grow.h.30": "1 Monat",
        "grow.h.365": "1 Jahr",
        "grow.interest": "Zinsen",
        "cal.unit.1": "1 Kalender = 1 Tag",
        "cal.unit.7": "1 Kalender = 1 Woche",
        "cal.unit.30": "1 Kalender = 1 Monat",
        "dot.unit.1": "1 Punkt = 1 Tag",
        "dot.unit.7": "1 Punkt = 1 Woche",
        "dot.unit.30": "1 Punkt = 1 Monat",
        "td.tower.checking": "Konto",
        "td.tower.td": "Truhe",
        "td.no_products": "Im Moment gibt es keine Schatztruhen-Angebote.",
        "td.preview": "Wenn du wartest, bekommst du {total}.",
        "td.preview.empty": "Wähle einen Betrag und wie lange du wartest.",
        "td.start": "Zumachen und warten",
        "td.locked": "Noch {n} Tage",
        "td.locked.1": "Noch 1 Tag",
        "td.locked.lesson": "Die Truhe bleibt zu, bis die Zeit um ist.",
        "td.ready": "Fertig! Deine Schatztruhe ist offen.",
        "td.ready.lesson": "Du hast gewartet, und dafür bekommst du {interest} extra.",
        "td.collect": "Abholen!",
        "td.collected": "Du hast {total} abgeholt!",
        "td.collected.lesson": "Das Geld ist jetzt wieder auf deinem Konto.",
        "td.seed.short": "Kurz",
        "td.seed.medium": "Mittel",
        "td.seed.long": "Lang",
        "td.none": "Du hast noch keine Schatztruhe.",
        # goals
        "goal.title": "Sparziele",
        "goal.lesson": "Ein Sparziel ist etwas, worauf du sparst. Die Plätze zeigen, wie nah du schon dran bist.",
        "goal.none": "Du hast noch kein Sparziel.",
        "goal.left": "Noch {amount} bis dahin",
        "goal.slot": "Ein Platz = {amount}",
        "goal.pct": "{pct} von 100 Prozent geschafft",
        "goal.reached": "Du hast genug gespart!",
        "goal.finish": "Ziel geschafft!",
        "goal.done": "Geschafft",
        "goal.done.msg": "Ziel geschafft!",
        "goal.done.hint": "Brauchst du dafür Geld von deinem Konto? Dann sag Mama oder Papa Bescheid.",
        "goal.delete": "Löschen",
        "goal.new": "Neues Sparziel",
        "goal.photo": "Foto machen",
        "goal.name": "Name (oder nur ein Foto)",
        "goal.price": "Wie viel Geld brauchst du?",
        "goal.create": "Sparziel anlegen",

        "offline.msg": "Du bist gerade nicht mit dem Internet verbunden.",
        "offline.hint": "Sobald du wieder verbunden bist, geht es weiter.",

        # errors
        "err.insufficient": "Du hast nicht genug Geld dafür.",
        "err.amount": "Das geht so nicht. Wähle einen Betrag.",
        "err.same_account": "Wähle ein anderes Konto.",
        "err.term_deposit_locked": "Die Schatztruhe ist noch zu. Du musst noch warten.",
        "err.already_collected": "Die Schatztruhe ist schon leer.",
        "err.term": "Diese Wartezeit gibt es nicht.",
        "err.no_shares": "Du hast nicht so viele Aktien.",
        "err.module_off": "Das ist für dich noch nicht freigeschaltet.",
        "err.forbidden": "Das darfst du nicht.",
        "err.pin_format": "Der Code muss aus 4 Zahlen bestehen.",
        "err.rate": "Der Zinssatz ist ungültig oder zu hoch (höchstens 100 Prozent pro Woche).",
        "err.locked": "Zu oft falsch getippt. Warte ein paar Minuten und versuche es dann noch einmal.",
        "err.notfound": "Das gibt es nicht.",
        "err.generic": "Huch, das hat nicht geklappt. Versuch es noch einmal!",
        "err.name": "Bitte gib einen Namen ein.",
        "err.goal_empty": "Gib dem Sparziel einen Namen oder mach ein Foto.",
        "err.goal_limit": "Du hast schon 3 Sparziele. Schaffe erst eins davon!",
        "err.goal_not_reached": "Dafür ist noch nicht genug Geld da.",
        "err.photo_size": "Das Foto ist zu groß.",
        "err.photo_type": "Mit diesem Foto geht es leider nicht.",

        # parents
        "par.title": "Eltern-Bereich",
        "par.kids": "Kinder",
        "par.module.term_deposit": "Schatztruhe (Festgeld)",
        "par.module.stocks": "Aktien",
        "par.add_kid": "Kind hinzufügen",
        "par.name": "Name",
        "par.avatar": "Bild",
        "par.pin": "Code (4 Zahlen)",
        "par.pin.reset": "Code zurücksetzen",
        "par.pin.reset.hint": "Das Kind kann den neuen Code danach selbst ändern.",
        "par.rate": "Zinsen (%)",
        "par.rate.hint": "Der Zeitraum gilt auch für die Auszahlung: Zinsen kommen alle 7 Tage, alle 30 Tage oder einmal im Jahr aufs Konto.",
        "par.rate.annual": "= {pct} % pro Jahr",
        "per.7": "pro Woche",
        "per.30": "pro Monat",
        "per.365": "pro Jahr",
        "par.products": "Schatztruhen-Angebote (Festgeld)",
        "par.product.name": "Name",
        "par.product.days": "Tage",
        "par.product.rate": "Zinsen (%)",
        "par.product.add": "Angebot hinzufügen",
        "par.product.hint": "Ein Angebot ändern: löschen und neu anlegen. Bestehende Schatztruhen behalten ihre Zinsen.",
        "par.product.delete": "Löschen",
        "par.book": "Geld buchen",
        "par.book.hint": "Positiv = einzahlen, negativ = abheben. Zum Beispiel Startkapital.",
        "par.book.account": "Konto",
        "par.book.amount": "Betrag in Euro",
        "par.book.note": "Notiz",
        "par.rules": "Taschengeld (Dauerauftrag)",
        "par.rule.kid": "Kind",
        "par.rule.amount": "Betrag in Euro",
        "par.rule.interval": "Wie oft",
        "par.rule.weekly": "Jede Woche",
        "par.rule.monthly": "Jeden Monat",
        "par.rule.weekday": "Wochentag",
        "par.rule.monthday": "Tag im Monat",
        **{f"wd.{i}": n for i, n in enumerate(["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"])},
        "par.rule.add": "Dauerauftrag anlegen",
        "par.rule.delete": "Löschen",
        "par.rule.edit": "Ändern",
        "par.rule.save": "Speichern",
        "par.goals": "Sparziele",
    },
}


def t(key: str, **kw) -> str:
    text = STRINGS[LOCALE].get(key, key)
    return text.format(**kw) if kw else text


def format_money(cents: int) -> str:
    euros, rest = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d} €"


def format_plain(cents: int) -> str:
    """'3,50': what a parent types into an amount field (no currency sign, no thousands dots)."""
    euros, rest = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{euros},{rest:02d}"


def format_percent(bp: int, unit: str = "") -> str:
    """Basis points -> '2,5' (German decimal comma), optionally followed by a unit."""
    text = f"{bp / 100:g}".replace(".", ",")
    return f"{text} {unit}".strip()


def format_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")
