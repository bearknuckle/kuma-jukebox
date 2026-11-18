"""試作ゲームラボの「遊んだ回数」カウンター。

POST /api/plays/<game_id>  回数を1増やす
GET  /api/plays            全ゲームの回数を返す

REDIS_URL があれば Render Key Value に保存、なければプロセス内メモリ。
無料の Key Value はメモリ保存なので、再起動で回数が0に戻る（ラボでは「最近の人気」として表示）。
"""
import os
import string
import threading

from flask import Flask, abort, jsonify, make_response, request

app = Flask(__name__)

LAB_ORIGINS = {o.strip() for o in os.getenv(
    "LAB_ORIGINS", "https://game-lab-oj9t.onrender.com").split(",") if o.strip()}
ID_CHARS = set(string.ascii_lowercase + string.digits + "-")
HASH_KEY = "lab:plays"
MAX_GAMES = 200

_plays = {}
_lock = threading.Lock()
_redis = None


def store():
    global _redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    if _redis is None:
        import redis
        _redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
    return _redis


def valid_id(game_id):
    return 0 < len(game_id) <= 40 and set(game_id) <= ID_CHARS


@app.after_request
def headers(response):
    origin = request.headers.get("Origin", "")
    if origin in LAB_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/plays")
def get_plays():
    try:
        s = store()
        if s is not None:
            counts = {k: int(v) for k, v in s.hgetall(HASH_KEY).items()}
        else:
            with _lock:
                counts = dict(_plays)
    except Exception:
        app.logger.exception("read failed")
        return jsonify({"plays": {}, "ok": False})
    return jsonify({"plays": counts, "ok": True})


@app.post("/api/plays/<game_id>")
def add_play(game_id):
    if not valid_id(game_id):
        abort(400)
    try:
        s = store()
        if s is not None:
            if s.hexists(HASH_KEY, game_id) or s.hlen(HASH_KEY) < MAX_GAMES:
                s.hincrby(HASH_KEY, game_id, 1)
        else:
            with _lock:
                if game_id in _plays or len(_plays) < MAX_GAMES:
                    _plays[game_id] = _plays.get(game_id, 0) + 1
    except Exception:
        app.logger.exception("write failed")
    return make_response("", 204)


@app.get("/healthz")
def health():
    return "ok"
