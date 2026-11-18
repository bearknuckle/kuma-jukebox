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
