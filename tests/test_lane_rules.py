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

@pytest.mark.parametrize("players,size", [(2, 24), (3, 20), (4, 16), (5, 16),
                                            (6, 16), (7, 12), (8, 12), (9, 8), (10, 8)])
def test_personal_deck_size_by_player_count(client, players, size):
    code_a = client.post("/api/rooms", json={"name": "A", "max_players": players}).get_json()
    code, host = code_a["room"]["code"], code_a["player_id"]
    client.post(f"/api/rooms/{code}/start", json={"player_id": host})
    room = server.ROOMS[code]
    game = room["game"]
    for p in room["players"]:
        # Each player was dealt `size` cards as their personal deck and
        # drew 4 into hand. CPUs have already submitted one card.
        held = len(game["hands"][p["id"]]) + (1 if p["id"] in game["submissions"] else 0)
        assert held == 4
        assert len(game["decks"][p["id"]]) == size - 4
    # Five of the undealt cards seed the lanes; the rest are unused.
    assert sum(len(lane) for lane in game["lanes"]) == 5
    assert len(game["unused"]) == 110 - players * size - 5


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
    # Hands are always 4 cards, refilled from each player's own deck, so a
    # game lasts as many turns as the personal deck size (24/16/8 here).
    for _ in range(200):
        if room["status"] != "playing":
            break
        server.submit_cpu_cards(room)
        server.resolve_online_turn(room)
    assert room["status"] == "finished"

    def all_cards():
        cards = [c for lane in game["lanes"] for c in lane]
        cards += [c for t in game["taken"].values() for c in t]
        cards += [c for h in game["hands"].values() for c in h]
        cards += [c for d in game["decks"].values() for c in d]
        cards += game["unused"]
        return sorted(c["n"] for c in cards)

    assert len(all_cards()) == 110
    assert len(set(all_cards())) == len(all_cards())
    assert all(1 <= len(lane) <= 5 for lane in game["lanes"])
    assert game["winner"]


def test_hands_stay_at_four_while_deck_remains(client):
    code_a = client.post("/api/rooms", json={"name": "A", "max_players": 4}).get_json()
    code, host = code_a["room"]["code"], code_a["player_id"]
    client.post(f"/api/rooms/{code}/start", json={"player_id": host})
    # Hand the human seat to a CPU so turns resolve automatically.
    client.post(f"/api/rooms/{code}/leave", json={"player_id": host})
    room = server.ROOMS[code]
    game = room["game"]
    # With 4 players each personal deck starts at 16 cards (12 after the
    # opening draw of 4); the CPU handover on leave may already have
    # resolved a turn (and drawn 1 more per player).
    decks_before = {p["id"]: len(game["decks"][p["id"]]) for p in room["players"]}
    assert all(n <= 16 - 4 for n in decks_before.values())
    for _ in range(10):
        server.submit_cpu_cards(room)
        server.resolve_online_turn(room)
        # Hands are refilled to 4 after every turn. CPUs immediately
        # submit one card for the next turn, so hand + submitted == 4.
        for p in room["players"]:
            held = len(game["hands"][p["id"]]) + (1 if p["id"] in game["submissions"] else 0)
            assert held == 4
        # Each resolved turn draws exactly one card per player,
        # from that player's own deck.
        for p in room["players"]:
            assert decks_before[p["id"]] - len(game["decks"][p["id"]]) == 1
        decks_before = {p["id"]: len(game["decks"][p["id"]]) for p in room["players"]}
