import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .i18n import LOCALE, STRINGS, t
from .routes import banking, login, parent, savings
from .web import BASE, Redirect, redirect, render


def _secret() -> str:
    if key := os.environ.get("KIDDYBANK_SECRET"):
        return key
    f = Path(".session_secret")  # persisted so restarts don't log everyone out
    try:
        with os.fdopen(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:  # owner-only, no race between workers
            fh.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return f.read_text()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # no public map of the routes
app.add_middleware(SessionMiddleware, secret_key=_secret(), same_site="lax", https_only=True, max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
for module in (login, banking, savings, parent):
    app.include_router(module.router)


@app.exception_handler(Redirect)
def _redirect(request: Request, exc: Redirect):
    return redirect(exc.url)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    """Kids get a page in their own words, not {"detail": "Not Found"}. Raise HTTPException(status, "i18n.key")."""
    key = exc.detail if exc.detail in STRINGS[LOCALE] else "err.notfound" if exc.status_code == 404 else "err.generic"
    return render(request, "error.html", exc.status_code, msg=t(key))


@app.exception_handler(RequestValidationError)
def _bad_form(request: Request, exc: RequestValidationError):
    return render(request, "error.html", 422, msg=t("err.generic"))
