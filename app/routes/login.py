"""Login, setup, logout, and the files the browser asks for by fixed path."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from .. import auth, festgeld, ledger
from ..auth import verify_pin
from ..ledger import LedgerError
from ..models import User
from ..web import BASE, db, get_today, redirect, render, sign_in, valid_pin

router = APIRouter()


# --- login -------------------------------------------------------------------------------------

@router.get("/")
def index(request: Request, s: Session = db):
    if not s.exec(select(User)).first():
        return redirect("/setup")
    user = s.get(User, request.session.get("uid") or 0)
    if not user:
        return redirect("/login")
    return redirect("/eltern" if user.role == "parent" else "/home")


@router.get("/setup")
def setup_form(request: Request, s: Session = db):
    if s.exec(select(User)).first():
        return redirect("/")
    return render(request, "setup.html")


@router.post("/setup")
def setup(request: Request, name: str = Form(...), pin: str = Form(...), s: Session = db,
          today: date = Depends(get_today)):
    if s.exec(select(User)).first():
        return redirect("/")
    try:
        valid_pin(pin)
        if not name.strip():
            raise LedgerError("err.name")
    except LedgerError as e:
        return render(request, "setup.html", 400, error=e.args[0])
    u = ledger.create_user(s, name.strip(), "parent", pin, "👪", today)
    festgeld.seed_default_products(s)
    sign_in(request, u)
    return redirect("/eltern")


@router.get("/login")
def login_picker(request: Request, s: Session = db):
    return render(request, "login.html", users=s.exec(select(User).order_by(User.role.desc(), User.id)).all())  # type: ignore[attr-defined]


@router.get("/login/{uid}")
def pin_form(request: Request, uid: int, s: Session = db):
    user = s.get(User, uid)
    if not user:
        return redirect("/login")
    return render(request, "pin.html", who=user)


@router.post("/login/{uid}")
def pin_submit(request: Request, uid: int, pin: str = Form(...), s: Session = db):
    user = s.get(User, uid)
    if not user:
        return redirect("/login")
    if auth.locked(user):
        return render(request, "pin.html", 200, who=user, error="err.locked")
    if not verify_pin(pin, user.pin_hash):
        auth.pin_failed(user)
        return render(request, "pin.html", 200, who=user, error="login.pin.wrong")
    auth.pin_ok(user)
    sign_in(request, user)
    return redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@router.get("/offline")
def offline(request: Request):
    return render(request, "offline.html")


@router.get("/sw.js")
def service_worker():
    # served from the root so its scope covers the whole app
    return FileResponse(BASE / "static" / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})
