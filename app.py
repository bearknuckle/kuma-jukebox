import hmac
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, abort, make_response, send_from_directory

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
CONTENT_DIR = os.path.join(os.path.dirname(__file__), "baron-nimmt")


def is_public_now():
    start = os.getenv("PUBLIC_START_JST", "").strip()
    end = os.getenv("PUBLIC_END_JST", "").strip()
    if not start or not end:
        return False
    try:
        start_at = datetime.fromisoformat(start).replace(tzinfo=JST)
        end_at = datetime.fromisoformat(end).replace(tzinfo=JST)
    except ValueError:
        return False
    now = datetime.now(JST)
    return start_at < end_at and start_at <= now < end_at


@app.before_request
def enforce_public_window():
    # Set PUBLIC_WINDOW_ENABLED=true to re-enable the scheduled public period.
    # Until then, the site remains publicly accessible.
    if os.getenv("PUBLIC_WINDOW_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    # Secret preview URLs are checked separately in the preview route.
    if request_path_is_preview():
        return None
    if not is_public_now():
        abort(404)


def request_path_is_preview():
    from flask import request
    return request.path.startswith("/preview/")


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/")
def home():
    return send_from_directory(CONTENT_DIR, "home.html")


@app.get("/catan")
def catan_tutorial():
    return send_from_directory(CONTENT_DIR, "catan.html")


@app.get("/acquire")
def acquire_tutorial():
    return send_from_directory(CONTENT_DIR, "acquire.html")


@app.get("/baron-nimmt")
def tutorial():
    return send_from_directory(CONTENT_DIR, "draft.html")


@app.get("/baron-nimmt/cpu")
def cpu_game():
    return send_from_directory(CONTENT_DIR, "cpu.html")


def valid_preview_token(token):
    expected = os.getenv("PREVIEW_URL_TOKEN", "")
    return bool(expected) and hmac.compare_digest(token, expected)


@app.get("/preview/<token>/baron-nimmt")
def preview_baron_nimmt(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "index.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/tutorial"')
    html = html.replace('href="/baron-nimmt/cpu"', 'href="/preview/' + token + '/cpu"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>/cpu")
def preview_cpu(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "cpu.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/tutorial"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>/tutorial")
def preview_tutorial(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "draft.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt/cpu"', 'href="/preview/' + token + '/cpu"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>")
def preview(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "home.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/baron-nimmt"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/healthz")
def health():
    # Health endpoint reveals no tutorial content or event schedule.
    return make_response("ok", 200)
