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


YT_API = "https://www.googleapis.com/youtube/v3/"


def yt_key():
    return os.getenv("YOUTUBE_API_KEY", "").strip()


def yt_api(path, **params):
    """YouTube Data API v3（公式）。キーは環境変数 YOUTUBE_API_KEY"""
    import urllib.parse
    params["key"] = yt_key()
    with urllib.request.urlopen(YT_API + path + "?" + urllib.parse.urlencode(params), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def yt_playlist_items(playlist_id, limit=500):
    out, token = [], ""
    while len(out) < limit:
        d = yt_api("playlistItems", part="snippet", playlistId=playlist_id, maxResults=50, pageToken=token)
        for it in d.get("items", []):
            sn = it.get("snippet", {})
            vid = (sn.get("resourceId") or {}).get("videoId")
            if vid and sn.get("title") not in ("Private video", "Deleted video"):
                out.append({"id": vid, "title": sn.get("title", "")})
        token = d.get("nextPageToken")
        if not token:
            break
    return out


def fetch_kuma_subs_api(channel, scraped_playlists):
    """APIで、トピックチャンネルのアルバム・シングルを全部集めて、その中の曲を返す"""
    playlists, errors = list(scraped_playlists), []
    # チャンネルの再生リスト
    try:
        token = ""
        for _ in range(10):
            d = yt_api("playlists", part="id", channelId=channel, maxResults=50, pageToken=token)
            playlists += [it["id"] for it in d.get("items", [])]
            token = d.get("nextPageToken")
            if not token:
                break
    except Exception as ex:
        errors.append("api-playlists:" + type(ex).__name__)
    # 検索（アルバム・シングルの再生リスト）
    try:
        token = ""
        for _ in range(4):
            d = yt_api("search", part="id", channelId=channel, type="playlist", maxResults=50, pageToken=token)
            playlists += [it["id"].get("playlistId") for it in d.get("items", []) if it.get("id", {}).get("playlistId")]
            token = d.get("nextPageToken")
            if not token:
                break
    except Exception as ex:
        errors.append("api-search:" + type(ex).__name__)
    # アップロード一覧
    uploads = []
    try:
        uploads = yt_playlist_items("UU" + channel[2:])
    except Exception as ex:
        errors.append("api-uploads:" + type(ex).__name__)
    playlists = [p for p in _uniq(playlists) if p and not p.startswith("UU")][:100]
    videos, seen = [], set()
    for v in uploads:
        if v["id"] not in seen:
            seen.add(v["id"]); videos.append(v)
    for pl in playlists:
        try:
            for v in yt_playlist_items(pl, 100):
                if v["id"] not in seen:
                    seen.add(v["id"]); videos.append(v)
        except Exception as ex:
            errors.append("api-items:" + type(ex).__name__)
    return {"videos": videos, "playlists": playlists, "errors": errors, "api": True}


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
    for tab in ("releases", ""):
        try:
            html = _get("https://www.youtube.com/channel/%s/%s" % (channel, tab))
            m = re.search(r"ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
            text = m.group(1) if m else html
            # アルバム・シングル（OLAK…）はリリース一覧とホームの両方から集める。動画IDはリリース一覧からだけ
            playlists += [p for p in PLAYLIST_ID.findall(text) if p.startswith("OLAK5uy_") or tab == "releases"]
            if tab == "releases":
                videos += VIDEO_ID.findall(text)
        except Exception as ex:
            errors.append(tab + ":" + type(ex).__name__)
    playlists = [p for p in _uniq(playlists) if p != "UU" + channel[2:]][:60]
    videos = _uniq(videos)[:300]
    if yt_key():
        try:
            d = fetch_kuma_subs_api(channel, playlists)
            if d["videos"]:
                d["errors"] = errors + d["errors"]
                return d
            errors += d["errors"]
        except Exception as ex:
            errors.append("api:" + type(ex).__name__)
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


KUMA_FPAG_CHANNEL = os.getenv("KUMA_FPAG_CHANNEL", "UCFvOwDfHVEBwu-GTv81da1g")
_fpag_cache = {"at": 0, "data": None}
PL_WITH_TITLE = re.compile(r'"playlistId":"((?:PL|OLAK5uy_)[A-Za-z0-9_-]{10,60})".{0,800}?"title":\{(?:"simpleText":"([^"]+)"|"runs":\[\{"text":"([^"]+)")', re.S)


@app.get("/api/kuma-playlists")
def kuma_playlists():
    """F-Pagチャンネルの再生リスト（ID・名前）。ジュークボックスで曲だけのリストを使うため"""
    with _kuma_lock:
        if _fpag_cache["data"] is None or time.time() - _fpag_cache["at"] > KUMA_CACHE_SEC:
            found, errors = {}, []
            try:
                html = _get("https://www.youtube.com/channel/%s/playlists" % KUMA_FPAG_CHANNEL)
                for pid, t1, t2 in PL_WITH_TITLE.findall(html):
                    found.setdefault(pid, t1 or t2)
            except Exception as ex:
                errors.append(type(ex).__name__)
            _fpag_cache.update(at=time.time(), data={
                "playlists": [{"id": k, "title": v} for k, v in found.items()][:50], "errors": errors})
        data = _fpag_cache["data"]
    return jsonify(data)


_info_cache = {}
ONE_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_seconds(text):
    m = ISO_DUR.fullmatch(text or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


@app.get("/api/yt-info")
def yt_info():
    """動画の長さ（秒）・ジャンル（categoryId）・ライブかどうか。APIキーがあるときだけ"""
    ids = [i for i in request.args.get("ids", "").split(",") if ONE_VIDEO.match(i)][:50]
    if not yt_key():
        return jsonify({"info": {}, "api": False})
    todo = [i for i in ids if i not in _info_cache]
    if todo:
        try:
            d = yt_api("videos", part="contentDetails,snippet", id=",".join(todo), maxResults=50)
            for it in d.get("items", []):
                sn = it.get("snippet", {})
                if len(_info_cache) < 5000:
                    _info_cache[it["id"]] = {"sec": iso_seconds(it.get("contentDetails", {}).get("duration")),
                                             "cat": sn.get("categoryId", ""), "title": sn.get("title", ""),
                                             "live": sn.get("liveBroadcastContent", "none")}
        except Exception:
            app.logger.exception("yt-info failed")
    return jsonify({"info": {i: _info_cache[i] for i in ids if i in _info_cache}, "api": True})


@app.get("/healthz")
def health():
    return "ok"
