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
        d = yt_api("playlistItems", part="snippet,contentDetails", playlistId=playlist_id, maxResults=50, pageToken=token)
        for it in d.get("items", []):
            sn = it.get("snippet", {})
            vid = (sn.get("resourceId") or {}).get("videoId")
            if vid and sn.get("title") not in ("Private video", "Deleted video"):
                pub = (it.get("contentDetails") or {}).get("videoPublishedAt") or sn.get("publishedAt", "")
                out.append({"id": vid, "title": sn.get("title", ""), "pub": pub})
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
    # 検索（曲そのもの）：トピックチャンネルの曲を新しい順・人気順・名前順で拾う
    searched = []
    for order in ("date", "viewCount", "title"):
        try:
            token = ""
            for _ in range(6):
                d = yt_api("search", part="snippet", channelId=channel, type="video", order=order,
                           maxResults=50, pageToken=token)
                for it in d.get("items", []):
                    vid = it.get("id", {}).get("videoId")
                    if vid:
                        sn = it.get("snippet") or {}
                        searched.append({"id": vid, "title": sn.get("title", ""), "pub": sn.get("publishedAt", "")})
                token = d.get("nextPageToken")
                if not token:
                    break
        except Exception as ex:
            errors.append("api-search-video:" + type(ex).__name__)
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
    for v in searched:
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


SUBS_KEY = "kuma:subs"           # 最新の一覧（JSON）
SUBS_TTL = 24 * 60 * 60          # 検索（APIの消費が大きい）は1日1回まで


def _load_subs():
    """保存しておいた一覧（Key Value）。なければメモリ"""
    r = store()
    if r is not None:
        try:
            raw = r.get(SUBS_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            app.logger.exception("load subs failed")
    return _kuma_cache["data"] and dict(_kuma_cache["data"], at=_kuma_cache["at"])


def _save_subs(data):
    _kuma_cache.update(at=data["at"], data=data)
    r = store()
    if r is not None:
        try:
            r.set(SUBS_KEY, json.dumps(data, ensure_ascii=False))
        except Exception:
            app.logger.exception("save subs failed")


def _merge_subs(old, new):
    """一度見つかった曲は消さない（APIの上限で検索に失敗しても減らない）"""
    if not old:
        return new
    seen, videos = set(), []
    for v in (new.get("videos") or []) + (old.get("videos") or []):
        if v.get("id") and v["id"] not in seen:
            seen.add(v["id"]); videos.append(v)
    playlists = _uniq((new.get("playlists") or []) + (old.get("playlists") or []))
    return dict(new, videos=videos, playlists=playlists)


@app.get("/api/kuma-subs")
def kuma_subs():
    with _kuma_lock:
        saved = _load_subs()
        age = time.time() - (saved or {}).get("at", 0)
        force = request.args.get("refresh") == "1" and age > 10 * 60
        # 検索がAPIの上限で失敗していたときは、2時間後にもう一度試す（上限は毎日リセットされる）
        failed = any("search" in e for e in (saved or {}).get("errors", []))
        ttl = 2 * 60 * 60 if failed else SUBS_TTL
        if not saved or age > ttl or force:
            new = fetch_kuma_subs()
            data = _merge_subs(saved, new)
            data["at"] = time.time()
            data["errors"] = new.get("errors", [])
            if data.get("videos") or not saved:
                _save_subs(data)
            else:
                data = saved
        else:
            data = saved
    return jsonify(data)


KUMA_FPAG_CHANNEL = os.getenv("KUMA_FPAG_CHANNEL", "UCFvOwDfHVEBwu-GTv81da1g")
_fpag_cache = {"at": 0, "data": None}
PL_WITH_TITLE = re.compile(r'"playlistId":"((?:PL|OLAK5uy_)[A-Za-z0-9_-]{10,60})".{0,800}?"title":\{(?:"simpleText":"([^"]+)"|"runs":\[\{"text":"([^"]+)")', re.S)


@app.get("/api/kuma-playlists")
def kuma_playlists():
    """F-Pag・KUMA MUSICの再生リスト（ID・名前）。APIキーがあれば公式APIで全部取る"""
    with _kuma_lock:
        if _fpag_cache["data"] is None or time.time() - _fpag_cache["at"] > KUMA_CACHE_SEC:
            found, errors = {}, []
            if yt_key():
                for ch in (KUMA_FPAG_CHANNEL, KUMA_TOPIC_CHANNEL):
                    try:
                        token = ""
                        for _ in range(10):
                            d = yt_api("playlists", part="snippet,contentDetails", channelId=ch, maxResults=50, pageToken=token)
                            for it in d.get("items", []):
                                found.setdefault(it["id"], {"title": (it.get("snippet") or {}).get("title", ""),
                                                            "count": (it.get("contentDetails") or {}).get("itemCount"),
                                                            "channel": ch})
                            token = d.get("nextPageToken")
                            if not token:
                                break
                    except Exception as ex:
                        errors.append("api:" + type(ex).__name__)
            if not found:
                try:
                    html = _get("https://www.youtube.com/channel/%s/playlists" % KUMA_FPAG_CHANNEL)
                    for pid, t1, t2 in PL_WITH_TITLE.findall(html):
                        found.setdefault(pid, {"title": t1 or t2, "count": None, "channel": KUMA_FPAG_CHANNEL})
                except Exception as ex:
                    errors.append(type(ex).__name__)
            _fpag_cache.update(at=time.time(), data={
                "playlists": [dict(v, id=k) for k, v in found.items()][:200], "errors": errors})
        data = _fpag_cache["data"]
    return jsonify(data)


_plist_cache = {}
PLAYLIST_OK = re.compile(r"^[A-Za-z0-9_-]{10,80}$")


@app.get("/api/playlist")
def playlist_items():
    """再生リストの曲を順番どおりに返す（公式API）"""
    pid = request.args.get("id", "")
    if not PLAYLIST_OK.match(pid) or not yt_key():
        return jsonify({"items": [], "api": bool(yt_key())})
    c = _plist_cache.get(pid)
    if not c or time.time() - c["at"] > 60 * 60:
        try:
            c = {"at": time.time(), "items": yt_playlist_items(pid, 300)}
            if len(_plist_cache) < 200:
                _plist_cache[pid] = c
        except Exception:
            app.logger.exception("playlist failed")
            c = {"items": []}
    return jsonify({"items": c["items"], "api": True})


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
    # 再生回数が古くならないよう、6時間たったら取り直す
    todo = [i for i in ids if i not in _info_cache or time.time() - _info_cache[i].get("at", 0) > KUMA_CACHE_SEC]
    if todo:
        try:
            d = yt_api("videos", part="contentDetails,snippet,statistics", id=",".join(todo), maxResults=50)
            for it in d.get("items", []):
                sn = it.get("snippet", {})
                st = it.get("statistics", {})
                if len(_info_cache) < 5000 or it["id"] in _info_cache:
                    _info_cache[it["id"]] = {"sec": iso_seconds(it.get("contentDetails", {}).get("duration")),
                                             "cat": sn.get("categoryId", ""), "title": sn.get("title", ""),
                                             "live": sn.get("liveBroadcastContent", "none"),
                                             "pub": sn.get("publishedAt", ""),
                                             "views": int(st["viewCount"]) if st.get("viewCount") else None,
                                             "at": time.time()}
        except Exception:
            app.logger.exception("yt-info failed")
    return jsonify({"info": {i: _info_cache[i] for i in ids if i in _info_cache}, "api": True})


# ===== ディスコグラフィー（リリースごとの作品一覧）=====
DISCO_KEY = "kuma:disco"
DISCO_TTL = 12 * 60 * 60
_disco_mem = {"at": 0, "data": None}
RELEASED = re.compile(r"Released on:\s*(\d{4}-\d{2}-\d{2})")


def parse_art_track(desc):
    """トピックチャンネルの曲の説明文から、アルバム名と発売日を取り出す
    （Provided to YouTube by … / 曲名 · アーティスト / アルバム名 / ℗ … / Released on: YYYY-MM-DD）"""
    lines = [l.strip() for l in (desc or "").splitlines()]
    album, date = "", ""
    m = RELEASED.search(desc or "")
    if m:
        date = m.group(1)
    for i, l in enumerate(lines):
        if " · " in l:
            rest = [x for x in lines[i + 1:] if x]
            if rest and not rest[0].startswith(("℗", "©", "Released on", "Auto-generated")):
                album = rest[0]
            break
    return album, date


def build_discography(videos):
    info = {}
    ids = [v["id"] for v in videos if ONE_VIDEO.match(v.get("id", ""))]
    for i in range(0, len(ids), 50):
        d = yt_api("videos", part="snippet,contentDetails,statistics", id=",".join(ids[i:i + 50]), maxResults=50)
        for it in d.get("items", []):
            sn = it.get("snippet", {})
            album, date = parse_art_track(sn.get("description", ""))
            info[it["id"]] = {"title": sn.get("title", ""), "album": album,
                              "date": date or (sn.get("publishedAt", "") or "")[:10],
                              "sec": iso_seconds(it.get("contentDetails", {}).get("duration")),
                              "views": int(it.get("statistics", {}).get("viewCount", 0) or 0)}
    groups = {}
    for vid in ids:
        f = info.get(vid)
        if not f:
            continue
        key = f["album"] or f["title"]
        g = groups.setdefault(key, {"title": key, "date": f["date"], "tracks": []})
        g["date"] = min(g["date"], f["date"]) if g["date"] and f["date"] else (g["date"] or f["date"])
        g["tracks"].append({"id": vid, "title": f["title"], "sec": f["sec"], "views": f["views"]})
    releases = []
    for g in groups.values():
        n = len(g["tracks"])
        g["type"] = "シングル" if n <= 3 else ("EP" if n <= 6 else "アルバム")
        g["cover"] = g["tracks"][0]["id"]
        g["views"] = sum(t["views"] for t in g["tracks"])
        releases.append(g)
    releases.sort(key=lambda g: g["date"] or "", reverse=True)
    return {"releases": releases, "count": len(releases), "tracks": len(info), "at": time.time()}


@app.get("/api/discography")
def discography():
    r = store()
    saved = None
    try:
        raw = r.get(DISCO_KEY) if r is not None else None
        saved = json.loads(raw) if raw else _disco_mem["data"]
    except Exception:
        saved = _disco_mem["data"]
    if saved and time.time() - saved.get("at", 0) < DISCO_TTL:
        return jsonify(saved)
    if not yt_key():
        return jsonify(saved or {"releases": [], "count": 0})
    subs = _load_subs() or {}
    try:
        data = build_discography(subs.get("videos") or [])
    except Exception:
        app.logger.exception("discography failed")
        return jsonify(saved or {"releases": [], "count": 0})
    if data["releases"] and (not saved or data["tracks"] >= saved.get("tracks", 0)):
        _disco_mem["data"] = data
        try:
            r is not None and r.set(DISCO_KEY, json.dumps(data, ensure_ascii=False))
        except Exception:
            app.logger.exception("save disco failed")
        return jsonify(data)
    return jsonify(saved or data)


@app.get("/healthz")
def health():
    return "ok"
