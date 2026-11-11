import hmac
import os
import secrets
import string
import threading
import time
from flask import jsonify, request
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, abort, make_response, send_from_directory

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
CONTENT_DIR = os.path.join(os.path.dirname(__file__), "baron-nimmt")

# Simple in-process room store. Render service is configured as a single instance.
ROOMS = {}
ROOM_LOCK = threading.RLock()
ROOM_TTL = 6 * 60 * 60
MAX_ROOMS = 100

def clean_rooms():
    now = time.time()
    expired = [code for code, room in ROOMS.items() if now - room["updated"] > ROOM_TTL]
    for code in expired:
        ROOMS.pop(code, None)

def public_room(room):
    return {
        "code": room["code"],
        "host_id": room["host_id"],
        "host_name": room["host_name"],
        "max_players": room["max_players"],
        "players": [{"id": p["id"], "name": p["name"], "is_host": p["id"] == room["host_id"]} for p in room["players"]],
        "status": room["status"],
        "created": room["created"]
    }

def room_code():
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(30):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in ROOMS:
            return code
    raise RuntimeError("room code generation failed")


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


@app.get("/carcassonne")
def carcassonne_quiz():
    return send_from_directory(CONTENT_DIR, "carcassonne.html")


@app.get("/acquire")
def acquire_tutorial():
    return send_from_directory(CONTENT_DIR, "acquire.html")


@app.get("/baron-nimmt")
def tutorial():
    return send_from_directory(CONTENT_DIR, "cpu.html")


@app.get("/baron-nimmt/online")
def online_game():
    return send_from_directory(CONTENT_DIR, "online.html")


@app.post("/api/rooms")
def create_room():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:16]
    try:
        max_players = int(data.get("max_players", 4))
    except (TypeError, ValueError):
        return jsonify(error="参加人数が正しくありません。"), 400
    if not name:
        return jsonify(error="プレイヤー名を入力してください。"), 400
    if not 2 <= max_players <= 10:
        return jsonify(error="参加人数は2〜10人にしてください。"), 400
    with ROOM_LOCK:
        clean_rooms()
        if len(ROOMS) >= MAX_ROOMS:
            return jsonify(error="現在ルームが満室です。時間をおいてお試しください。"), 503
        code = room_code()
        player_id = secrets.token_urlsafe(18)
        now = time.time()
        room = {
            "code": code, "host_id": player_id, "host_name": name,
            "max_players": max_players,
            "players": [{"id": player_id, "name": name, "joined": now}],
            "status": "waiting", "created": now, "updated": now
        }
        ROOMS[code] = room
        return jsonify(room=public_room(room), player_id=player_id)


@app.post("/api/rooms/<code>/join")
def join_room(code):
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:16]
    if not name:
        return jsonify(error="プレイヤー名を入力してください。"), 400
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが見つかりません。番号を確認してください。"), 404
        if room["status"] != "waiting":
            return jsonify(error="このルームでは募集を終了しています。"), 409
        if len(room["players"]) >= room["max_players"]:
            return jsonify(error="このルームは満員です。"), 409
        player_id = secrets.token_urlsafe(18)
        room["players"].append({"id": player_id, "name": name, "joined": time.time()})
        room["updated"] = time.time()
        return jsonify(room=public_room(room), player_id=player_id)


@app.get("/api/rooms/<code>")
def get_room(code):
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが終了したか、見つかりません。"), 404
        room["updated"] = time.time()
        return jsonify(room=public_room(room))


@app.post("/api/rooms/<code>/leave")
def leave_room(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    with ROOM_LOCK:
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(ok=True)
        room["players"] = [p for p in room["players"] if p["id"] != player_id]
        if not room["players"] or player_id == room["host_id"]:
            ROOMS.pop(code.upper(), None)
        else:
            room["updated"] = time.time()
        return jsonify(ok=True)


@app.post("/api/rooms/<code>/start")
def start_room(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    with ROOM_LOCK:
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが見つかりません。"), 404
        if player_id != room["host_id"]:
            return jsonify(error="開始できるのはルーム作成者です。"), 403
        if len(room["players"]) < 2:
            return jsonify(error="対戦には2人以上必要です。"), 409
        room["status"] = "starting"
        room["updated"] = time.time()
        return jsonify(room=public_room(room))


@app.get("/baron-nimmt/cpu")
def cpu_game():
    return send_from_directory(CONTENT_DIR, "cpu.html")


@app.get("/baron-nimmt/cards.json")
def baron_cards():
    return send_from_directory(CONTENT_DIR, "cards.json", mimetype="application/json")


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
