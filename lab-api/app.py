"""試作ゲームラボの「遊んだ回数」カウンター。

POST /api/plays/<game_id>  回数を1増やす
GET  /api/plays            全ゲームの回数を返す
GET  /api/kuma-subs        KUMA MUSIC（トピックチャンネル）のサブスク曲の動画ID・再生リストIDを返す（ジュークボックス用）

REDIS_URL があれば Render Key Value に保存、なければプロセス内メモリ。
無料の Key Value はメモリ保存なので、再起動で回数が0に戻る（ラボでは「最近の人気」として表示）。
"""
import json
import os
import re
import string
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

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


# ===== KUMA MUSIC ジュークボックス用 =====
KUMA_TOPIC_CHANNEL = os.getenv("KUMA_TOPIC_CHANNEL", "UCRlaIisipjF9qL1zYkMMCug")
KUMA_CACHE_SEC = 6 * 60 * 60
_kuma_cache = {"at": 0, "data": None}
_kuma_lock = threading.Lock()
VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
PLAYLIST_ID = re.compile(r'"playlistId":"((?:OLAK5uy_|PL|UU)[A-Za-z0-9_-]{10,60})"')


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ja,en;q=0.8",
        "Cookie": "CONSENT=YES+1; SOCS=CAI",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def _uniq(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def fetch_kuma_subs(channel=None):
    channel = channel or KUMA_TOPIC_CHANNEL
    videos, titles, playlists, errors = [], {}, [], []
    # 1. RSS（新しい15曲まで。曲名つき）
    try:
        root = ET.fromstring(_get("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel))
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        for e in root.findall("a:entry", ns):
            vid = e.findtext("yt:videoId", default="", namespaces=ns)
            if vid:
                videos.append(vid)
                titles[vid] = e.findtext("a:title", default="", namespaces=ns)
    except Exception as ex:
        errors.append("rss:" + type(ex).__name__)
    # 2. チャンネルページ（リリース一覧・動画一覧）から再生リストIDと動画IDを拾う
    for tab in ("releases", "videos", ""):
        try:
            html = _get("https://www.youtube.com/channel/%s/%s" % (channel, tab))
            m = re.search(r"ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
            text = m.group(1) if m else html
            playlists += PLAYLIST_ID.findall(text)
            videos += VIDEO_ID.findall(text)
        except Exception as ex:
            errors.append(tab + ":" + type(ex).__name__)
    playlists = [p for p in _uniq(playlists) if p != "UU" + channel[2:]][:60]
    videos = _uniq(videos)[:300]
    return {"videos": [{"id": v, "title": titles.get(v, "")} for v in videos],
            "playlists": playlists, "errors": errors}


@app.get("/api/kuma-subs")
def kuma_subs():
    with _kuma_lock:
        fresh = _kuma_cache["data"] is not None and time.time() - _kuma_cache["at"] < KUMA_CACHE_SEC
        if not fresh or (request.args.get("refresh") == "1" and time.time() - _kuma_cache["at"] > 60):
            data = fetch_kuma_subs()
            if data["videos"] or data["playlists"] or _kuma_cache["data"] is None:
                _kuma_cache.update(at=time.time(), data=data)
        data = _kuma_cache["data"]
    return jsonify(data)


@app.get("/healthz")
def health():
    return "ok"
