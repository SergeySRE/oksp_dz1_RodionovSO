from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.config import settings
from app.pages import router as pages_router
from app.observation import SQLObservationMiddleware

if len(settings.session_secret) < 32:
    raise RuntimeError("Set SESSION_SECRET to a random string of at least 32 characters")

app = FastAPI(title="Запись на приём — sergey_rodionov", version="1.0.0")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                   session_cookie="sergey_rodionov_session", same_site="lax",
                   https_only=settings.https_only, max_age=8 * 60 * 60)
app.add_middleware(SQLObservationMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(api_router)
app.include_router(pages_router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exception: HTTPException):
    if exception.status_code == 401 and not request.url.path.startswith("/api/"):
        return RedirectResponse("/login", status_code=303)
    return await http_exception_handler(request, exception)
