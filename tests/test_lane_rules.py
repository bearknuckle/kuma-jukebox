"""Lane rules (placement and the three take conditions) for both
CPU-resolved cards and human lane choices."""
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


def make_room(lanes, players):
    """players: list of (id, is_cpu). Builds a playing room with given lanes."""
    room = {
        "code": "TEST01", "host_id": players[0][0], "host_name": players[0][0],
        "max_players": len(players), "status": "playing",
        "players": [{"id": pid, "name": pid, "is_cpu": cpu, "joined": 0} for pid, cpu in players],
        "created": 0, "updated": 0,
    }
    room["game"] = {
        "turn": 1, "lanes": lanes,
        "hands": {pid: [card(200 + i, ["red"])] for i, (pid, _) in enumerate(players)},
        "taken": {pid: [] for pid, _ in players},
        "submissions": {}, "submission_lanes": {}, "last_log": [], "winner": None,
        "resolution_queue": [], "pending_choice": None,
    }
    server.ROOMS[room["code"]] = room
    return room


def filler_lanes():
    # Lanes 2-5 end in black so they never match red/blue test cards.
    return [[card(150 + i, ["black"])] for i in range(4)]


def resolve_by_cpu(lane0, played):
    room = make_room([lane0] + filler_lanes(), [("cpu", True), ("h", False)])
    game = room["game"]
    game["resolution_queue"] = [("cpu", played, None)]
    server.continue_online_resolution(room)
    return game


def resolve_by_human(client, lane0, played, lane_index=0):
    room = make_room([lane0] + filler_lanes(), [("h", False), ("cpu", True)])
    game = room["game"]
    game["resolution_queue"] = [("h", played, None)]
    server.continue_online_resolution(room)
    assert game["pending_choice"]["player_id"] == "h"
    r = client.post("/api/rooms/TEST01/choose-lane", json={"player_id": "h", "lane_index": lane_index})
    assert r.status_code == 200, r.get_json()
    return game


def nums(cards):
    return [c["n"] for c in cards]


# ---- normal placement ----

def test_cpu_places_on_matching_lane():
    game = resolve_by_cpu([card(1, ["red"])], card(2, ["red"]))
    assert nums(game["lanes"][0]) == [1, 2]
    assert game["taken"]["cpu"] == []


def test_human_places_on_matching_lane(client):
    game = resolve_by_human(client, [card(1, ["red"])], card(2, ["red"]))
    assert nums(game["lanes"][0]) == [1, 2]
    assert game["taken"]["h"] == []


# ---- 6th card: take the 5 cards, played card starts the lane ----

FIVE = [card(i, ["red"]) for i in range(1, 6)]


def test_cpu_sixth_card_takes_lane():
    game = resolve_by_cpu([dict(c) for c in FIVE], card(6, ["red"]))
    assert nums(game["taken"]["cpu"]) == [1, 2, 3, 4, 5]
    assert nums(game["lanes"][0]) == [6]


def test_human_sixth_card_takes_lane(client):
    game = resolve_by_human(client, [dict(c) for c in FIVE], card(6, ["red"]))
    assert nums(game["taken"]["h"]) == [1, 2, 3, 4, 5]
    assert nums(game["lanes"][0]) == [6]


# ---- 6+ marks of one color: take the previous cards ----

def heavy_lane():
    # 5 red marks spread over 2 cards; playing a red card makes 6.
    return [card(1, ["red", "red", "red"]), card(2, ["red", "red"])]


def test_cpu_six_same_color_takes_lane():
    game = resolve_by_cpu(heavy_lane(), card(3, ["red"]))
    assert nums(game["taken"]["cpu"]) == [1, 2]
    assert nums(game["lanes"][0]) == [3]


def test_human_six_same_color_takes_lane(client):
    game = resolve_by_human(client, heavy_lane(), card(3, ["red"]))
    assert nums(game["taken"]["h"]) == [1, 2]
    assert nums(game["lanes"][0]) == [3]


# ---- no matching lane: take a whole lane ----

def test_cpu_no_match_takes_cheapest_lane():
    lane0 = [card(1, ["red"], penalty=5)]
    room = make_room([lane0] + filler_lanes(), [("cpu", True), ("h", False)])
    game = room["game"]
    game["lanes"][3] = [card(160, ["black"], penalty=0)]
    game["resolution_queue"] = [("cpu", card(9, ["blue"]), None)]
    server.continue_online_resolution(room)
    assert nums(game["taken"]["cpu"]) == [160]
    assert nums(game["lanes"][3]) == [9]


def test_human_no_match_takes_chosen_lane(client):
    game = resolve_by_human(client, [card(1, ["red"]), card(2, ["red"])], card(9, ["blue"]), lane_index=0)
    assert nums(game["taken"]["h"]) == [1, 2]
    assert nums(game["lanes"][0]) == [9]


def test_human_cannot_place_on_non_matching_lane(client):
    room = make_room([[card(1, ["red"])]] + filler_lanes(), [("h", False), ("cpu", True)])
    game = room["game"]
    game["resolution_queue"] = [("h", card(2, ["red"]), None)]
    server.continue_online_resolution(room)
    r = client.post("/api/rooms/TEST01/choose-lane", json={"player_id": "h", "lane_index": 1})
    assert r.status_code == 400
    assert game["pending_choice"] is not None


# ---- leaving while it is your turn to place ----

def test_leaving_during_place_choice_places_instead_of_taking(client):
    room = make_room([[card(1, ["red"])]] + filler_lanes(), [("h", False), ("h2", False)])
    game = room["game"]
    game["resolution_queue"] = [("h", card(2, ["red"]), None)]
    server.continue_online_resolution(room)
    assert game["pending_choice"]["mode"] == "place"
    client.post("/api/rooms/TEST01/leave", json={"player_id": "h"})
    # The card could be placed, so the replacement CPU must place it, not take a lane.
    assert nums(game["lanes"][0]) == [1, 2]
    assert game["taken"]["h"] == []
    assert game["pending_choice"] is None


# ---- full game: no card is ever created or lost ----

@pytest.mark.parametrize("players", [2, 5, 10])
def test_full_cpu_game_conserves_cards(client, players):
    code_a = client.post("/api/rooms", json={"name": "A", "max_players": players}).get_json()
    code, host = code_a["room"]["code"], code_a["player_id"]
    client.post(f"/api/rooms/{code}/start", json={"player_id": host})
    # Hand the human seat to a CPU so the whole game resolves automatically.
    client.post(f"/api/rooms/{code}/leave", json={"player_id": host})
    room = server.ROOMS[code]
    game = room["game"]
    # With no humans left, nothing triggers the next turn, so drive it here.
    for _ in range(30):
        if room["status"] != "playing":
            break
        server.submit_cpu_cards(room)
        server.resolve_online_turn(room)
    assert room["status"] == "finished"

    def all_cards():
        cards = [c for lane in game["lanes"] for c in lane]
        cards += [c for t in game["taken"].values() for c in t]
        cards += [c for h in game["hands"].values() for c in h]
        return sorted(c["n"] for c in cards)

    hand_size = {2: 24, 5: 16, 10: 8}[players]
    assert len(all_cards()) == players * hand_size + 5
    assert len(set(all_cards())) == len(all_cards())
    assert all(1 <= len(lane) <= 5 for lane in game["lanes"])
    assert game["winner"]
