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
def enforce_event_window():
    if not is_public_now():
        abort(404)

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
    return send_from_directory(CONTENT_DIR, "draft.html")

@app.get("/baron-nimmt")
def tutorial():
    return send_from_directory(CONTENT_DIR, "draft.html")

@app.get("/healthz")
def health():
    # Health endpoint reveals no tutorial content or event schedule.
    return make_response("ok", 200)
