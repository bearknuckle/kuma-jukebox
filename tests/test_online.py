import pytest

import app as server


@pytest.fixture
def client():
    server.ROOMS.clear()
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c
    server.ROOMS.clear()


def card(n, colors, penalty=1):
    return {"n": n, "penalty": penalty, "cs": list(colors)}


def create(client, name="A", max_players=2):
    r = client.post("/api/rooms", json={"name": name, "max_players": max_players})
    assert r.status_code == 200
    d = r.get_json()
    return d["room"]["code"], d["player_id"]


def join(client, code, name):
    r = client.post(f"/api/rooms/{code}/join", json={"name": name})
    assert r.status_code == 200
    return r.get_json()["player_id"]


def started_two_player_game(client):
    """Two humans; every lane ends in red, so blue cards force a lane choice."""
    code, a = create(client, "A", 2)
    b = join(client, code, "B")
    assert client.post(f"/api/rooms/{code}/start", json={"player_id": a}).status_code == 200
    game = server.ROOMS[code]["game"]
    game["lanes"] = [[card(100 + i, ["red"])] for i in range(5)]
    game["hands"][a] = [card(1, ["blue"]), card(3, ["red"])]
    game["hands"][b] = [card(2, ["blue"]), card(4, ["red"])]
    return code, a, b


def play(client, code, pid, n):
    return client.post(f"/api/rooms/{code}/play", json={"player_id": pid, "card_number": n})


# --- Bug 1: no submissions while the previous turn is still being resolved ---

def test_cannot_play_or_cancel_while_lane_choice_pending(client):
    code, a, b = started_two_player_game(client)
    assert play(client, code, a, 1).status_code == 200
    assert play(client, code, b, 2).status_code == 200

    game = server.ROOMS[code]["game"]
    assert game["pending_choice"]["player_id"] == a
    assert len(game["resolution_queue"]) == 2

    # B is waiting for A; B must not be able to submit the next card yet.
    assert play(client, code, b, 4).status_code == 409
    assert client.post(f"/api/rooms/{code}/cancel", json={"player_id": b}).status_code == 409
    # The chooser cannot submit either.
    assert play(client, code, a, 3).status_code == 409
    assert [c["n"] for c in game["hands"][b]] == [4]
    assert game["submissions"] == {}


def test_queued_cards_are_not_lost_and_next_turn_opens(client):
    code, a, b = started_two_player_game(client)
    play(client, code, a, 1)
    play(client, code, b, 2)

    r = client.post(f"/api/rooms/{code}/choose-lane", json={"player_id": a, "lane_index": 0})
    assert r.status_code == 200
    game = server.ROOMS[code]["game"]
    # B's queued card is still there and now it is B's turn to choose.
    assert game["pending_choice"]["player_id"] == b
    assert game["pending_choice"]["card"]["n"] == 2
    assert play(client, code, a, 3).status_code == 409

    # A's blue card now ends lane 1, so B's blue card is placed there.
    assert game["pending_choice"]["mode"] == "place"
    assert client.post(f"/api/rooms/{code}/choose-lane", json={"player_id": b, "lane_index": 1}).status_code == 400
    r = client.post(f"/api/rooms/{code}/choose-lane", json={"player_id": b, "lane_index": 0})
    assert r.status_code == 200
    assert game["pending_choice"] is None
    assert game["resolution_queue"] == []
    assert [c["n"] for c in game["taken"][a]] == [100]
    assert [c["n"] for c in game["lanes"][0]] == [1, 2]

    # Resolution finished: the next turn accepts cards again.
    assert play(client, code, a, 3).status_code == 200
    assert client.post(f"/api/rooms/{code}/cancel", json={"player_id": a}).status_code == 200


# --- Bug 2: leaving a waiting room frees the seat ---

def test_leave_waiting_room_frees_seat(client):
    code, a = create(client, "A", 2)
    b = join(client, code, "B")
    assert client.post(f"/api/rooms/{code}/leave", json={"player_id": b}).status_code == 200
    room = server.ROOMS[code]
    assert [p["name"] for p in room["players"]] == ["A"]
    assert not any(p.get("is_cpu") for p in room["players"])
    # The seat is open again.
    join(client, code, "C")


def test_host_leaving_waiting_room_transfers_host(client):
    code, a = create(client, "A", 3)
    b = join(client, code, "B")
    client.post(f"/api/rooms/{code}/leave", json={"player_id": a})
    room = server.ROOMS[code]
    assert room["host_id"] == b
    assert room["host_name"] == "B"
    assert client.post(f"/api/rooms/{code}/start", json={"player_id": b}).status_code == 200


def test_last_player_leaving_waiting_room_closes_it(client):
    code, a = create(client, "A", 2)
    r = client.post(f"/api/rooms/{code}/leave", json={"player_id": a})
    assert r.get_json().get("room_closed") is True
    assert code not in server.ROOMS
    assert client.get(f"/api/rooms/{code}").status_code == 404


def test_leaving_during_game_still_hands_seat_to_cpu(client):
    code, a, b = started_two_player_game(client)
    client.post(f"/api/rooms/{code}/leave", json={"player_id": b})
    player = next(p for p in server.ROOMS[code]["players"] if p["id"] == b)
    assert player["is_cpu"] is True
