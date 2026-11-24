import app as m


def c():
    m._plays.clear()
    return m.app.test_client()


def test_counts():
    t = c()
    assert t.post("/api/plays/onechord").status_code == 204
    t.post("/api/plays/onechord"); t.post("/api/plays/uwasa-kabu")
    assert t.get("/api/plays").get_json() == {"ok": True, "plays": {"onechord": 2, "uwasa-kabu": 1}}


def test_bad_ids():
    t = c()
    assert t.post("/api/plays/Bad_ID!").status_code == 400
    assert t.post("/api/plays/" + "a" * 41).status_code == 400


def test_cors():
    t = c()
    assert t.get("/api/plays", headers={"Origin": "https://game-lab-oj9t.onrender.com"}).headers["Access-Control-Allow-Origin"]
    assert "Access-Control-Allow-Origin" not in t.get("/api/plays", headers={"Origin": "https://x.example"}).headers


def test_kuma_subs(monkeypatch):
    rss = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<entry><yt:videoId>AAAAAAAAAAA</yt:videoId><title>ボードゲームと猫</title></entry></feed>"""
    page = 'var ytInitialData = {"a":{"playlistId":"OLAK5uy_abcdefghijklmnop"},"b":{"videoId":"BBBBBBBBBBB"}};</script>'
    monkeypatch.setattr(m, "_get", lambda url: rss if "feeds" in url else page)
    m._kuma_cache.update(at=0, data=None)
    d = c().get("/api/kuma-subs").get_json()
    assert d["videos"][0] == {"id": "AAAAAAAAAAA", "title": "ボードゲームと猫"}
    assert {"id": "BBBBBBBBBBB", "title": ""} in d["videos"]
    assert d["playlists"] == ["OLAK5uy_abcdefghijklmnop"]



def test_iso_seconds():
    assert m.iso_seconds("PT3M25S") == 205
    assert m.iso_seconds("PT1H2M") == 3720
    assert m.iso_seconds("P0D") == 0


def test_yt_info_without_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert c().get("/api/yt-info?ids=AAAAAAAAAAA").get_json() == {"info": {}, "api": False}
