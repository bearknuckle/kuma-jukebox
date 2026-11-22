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


def make_room(lanes, players, variation="none"):
    """players: list of (id, is_cpu). Builds a playing room with given lanes."""
    room = {
        "code": "TEST01", "host_id": players[0][0], "host_name": players[0][0],
        "max_players": len(players), "status": "playing", "variation": variation,
        "players": [{"id": pid, "name": pid, "is_cpu": cpu, "joined": 0} for pid, cpu in players],
        "created": 0, "updated": 0,
    }
    room["game"] = {
        "turn": 1, "lanes": lanes, "variation": variation,
        "hands": {pid: [card(200 + i, ["red"])] for i, (pid, _) in enumerate(players)},
        "decks": {pid: [] for pid, _ in players}, "unused": [],
        "taken": {pid: [] for pid, _ in players},
        "submissions": {}, "submission_lanes": {}, "last_log": [], "winner": None,
        "resolution_queue": [], "pending_choice": None,
    }
    server.ROOMS[room["code"]] = room
    return room


def filler_lanes():
    # Lanes 2-5 end in black so they never match red/blue test cards.
    return [[card(150 + i, ["black"])] for i in range(4)]


def resolve_by_cpu(lane0, played, variation="none"):
    room = make_room([lane0] + filler_lanes(), [("cpu", True), ("h", False)], variation=variation)
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
    # Each player plays one card per turn from a personal deck of
    # 24/16/8 cards, so a game lasts that many turns.
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


def _started_all_cpu_game(client, players=4):
    code_a = client.post("/api/rooms", json={"name": "A", "max_players": players}).get_json()
    code, host = code_a["room"]["code"], code_a["player_id"]
    client.post(f"/api/rooms/{code}/start", json={"player_id": host})
    # Hand the human seat to a CPU so turns resolve automatically. The
    # handover itself resolves the first turn.
    client.post(f"/api/rooms/{code}/leave", json={"player_id": host})
    return server.ROOMS[code]


def _held(game, room):
    return {p["id"]: len(game["hands"][p["id"]]) + (1 if p["id"] in game["submissions"] else 0)
            for p in room["players"]}


def test_no_refill_until_hand_is_played_out(client):
    room = _started_all_cpu_game(client, players=4)
    game = room["game"]
    # Turn 1 is done: everyone played 1 of their 4 cards. Hands are NOT
    # refilled and no deck card has been drawn.
    assert all(n == 3 for n in _held(game, room).values())
    assert all(len(game["decks"][p["id"]]) == 12 for p in room["players"])
    # Turns 2 and 3: hands keep shrinking, decks stay untouched.
    for expected in (2, 1):
        server.submit_cpu_cards(room)
        server.resolve_online_turn(room)
        assert all(n == expected for n in _held(game, room).values())
        assert all(len(game["decks"][p["id"]]) == 12 for p in room["players"])
    # Turn 4 plays out the hand: only now does each player draw 4 new
    # cards from their own deck.
    server.submit_cpu_cards(room)
    server.resolve_online_turn(room)
    assert all(n == 4 for n in _held(game, room).values())
    assert all(len(game["decks"][p["id"]]) == 8 for p in room["players"])


def test_refill_only_touches_empty_hands():
    room = make_room([[card(1, ["red"])]] + filler_lanes(), [("a", True), ("b", True)])
    game = room["game"]
    game["hands"]["a"] = []
    game["decks"]["a"] = [card(11, ["red"]), card(12, ["red"]), card(13, ["red"]),
                           card(14, ["red"]), card(15, ["red"])]
    game["hands"]["b"] = [card(21, ["red"])]
    game["decks"]["b"] = [card(22, ["red"]), card(23, ["red"])]
    server.draw_online_hands(room)
    assert len(game["hands"]["a"]) == 4
    assert len(game["decks"]["a"]) == 1
    # A hand that still holds cards is left alone.
    assert nums(game["hands"]["b"]) == [21]
    assert len(game["decks"]["b"]) == 2


# ---- variation: color matters (no take on the 6th card) ----

def sparse_five_lane():
    # 5 cards, only the last one is red, so red marks stay below 6.
    return [card(1, ["black"]), card(2, ["black"]), card(3, ["black"]),
            card(4, ["black"]), card(5, ["red"])]


def test_color_variation_sixth_card_does_not_take():
    game = resolve_by_cpu(sparse_five_lane(), card(6, ["red"]), variation="color")
    assert nums(game["lanes"][0]) == [1, 2, 3, 4, 5, 6]
    assert game["taken"]["cpu"] == []


def test_color_variation_still_takes_on_six_marks():
    game = resolve_by_cpu(heavy_lane(), card(3, ["red"]), variation="color")
    assert nums(game["taken"]["cpu"]) == [1, 2]
    assert nums(game["lanes"][0]) == [3]


def test_color_variation_no_match_still_takes_a_lane():
    room = make_room([[card(1, ["red"], penalty=5)]] + filler_lanes(),
                     [("cpu", True), ("h", False)], variation="color")
    game = room["game"]
    game["lanes"][3] = [card(160, ["black"], penalty=0)]
    game["resolution_queue"] = [("cpu", card(9, ["blue"]), None)]
    server.continue_online_resolution(room)
    assert nums(game["taken"]["cpu"]) == [160]
    assert nums(game["lanes"][3]) == [9]


def test_base_sixth_card_takes_even_without_six_marks():
    game = resolve_by_cpu(sparse_five_lane(), card(6, ["red"]))
    assert nums(game["taken"]["cpu"]) == [1, 2, 3, 4, 5]
    assert nums(game["lanes"][0]) == [6]


# ---- variation: gather the cows ----

def test_cows_no_match_goes_to_bottom_of_own_deck():
    room = make_room([[card(1, ["red"])]] + filler_lanes(),
                     [("cpu", True), ("h", False)], variation="cows")
    game = room["game"]
    game["decks"]["cpu"] = [card(50, ["black"]), card(51, ["black"])]
    game["resolution_queue"] = [("cpu", card(9, ["blue"]), None)]
    server.continue_online_resolution(room)
    assert game["taken"]["cpu"] == []
    # The played card is now at the bottom (front) of the player's deck.
    assert nums(game["decks"]["cpu"]) == [9, 50, 51]
    assert nums(game["lanes"][0]) == [1]


def test_cows_human_no_match_needs_no_lane_choice(client):
    room = make_room([[card(1, ["red"])]] + filler_lanes(),
                     [("h", False), ("cpu", True)], variation="cows")
    game = room["game"]
    game["resolution_queue"] = [("h", card(9, ["blue"]), None)]
    server.continue_online_resolution(room)
    assert game["pending_choice"] is None
    assert nums(game["decks"]["h"]) == [9]


def test_cows_last_card_played_ends_game_even_if_it_goes_under_deck():
    # Both players hold exactly one card and neither matches a lane, so
    # both cards go under their decks. The round still counts as the
    # round in which they played their last cards: the game must end
    # instead of cycling the same two cards forever.
    room = make_room([[card(1, ["red"])]] + filler_lanes(),
                     [("a", True), ("b", True)], variation="cows")
    game = room["game"]
    game["hands"]["a"] = []
    game["hands"]["b"] = []
    game["resolution_queue"] = [("a", card(9, ["blue"]), None), ("b", card(10, ["blue"]), None)]
    server.continue_online_resolution(room)
    assert room["status"] == "finished"
    assert sorted(game["winner"]) == ["a", "b"]  # no cows gathered: tie


def test_cows_sixth_card_still_takes():
    game = resolve_by_cpu([dict(c) for c in FIVE], card(6, ["red"]), variation="cows")
    assert nums(game["taken"]["cpu"]) == [1, 2, 3, 4, 5]
    assert nums(game["lanes"][0]) == [6]


def test_cows_game_ends_when_one_player_runs_out_and_most_marks_win():
    room = make_room([[card(1, ["red"])]] + filler_lanes(),
                     [("a", True), ("b", True)], variation="cows")
    game = room["game"]
    game["hands"]["a"] = []
    game["decks"]["a"] = []
    game["hands"]["b"] = [card(30, ["red"])]
    game["taken"]["a"] = [card(41, ["red"], penalty=7)]
    game["taken"]["b"] = [card(42, ["red"], penalty=2)]
    server.finish_online_game_if_needed(room)
    assert room["status"] == "finished"
    assert game["winner"] == ["a"]


def test_base_game_ends_only_when_everyone_is_out_and_fewest_marks_win():
    room = make_room([[card(1, ["red"])]] + filler_lanes(), [("a", True), ("b", True)])
    game = room["game"]
    game["hands"]["a"] = []
    game["decks"]["a"] = []
    game["hands"]["b"] = [card(30, ["red"])]
    server.finish_online_game_if_needed(room)
    assert room["status"] == "playing"
    game["hands"]["b"] = []
    game["taken"]["a"] = [card(41, ["red"], penalty=7)]
    game["taken"]["b"] = [card(42, ["red"], penalty=2)]
    server.finish_online_game_if_needed(room)
    assert room["status"] == "finished"
    assert game["winner"] == ["b"]


@pytest.mark.parametrize("variation", ["none", "color", "cows"])
@pytest.mark.parametrize("players", [2, 5, 10])
def test_full_cpu_game_conserves_cards_with_variations(client, players, variation):
    r = client.post("/api/rooms", json={"name": "A", "max_players": players, "variation": variation})
    assert r.status_code == 200
    code, host = r.get_json()["room"]["code"], r.get_json()["player_id"]
    client.post(f"/api/rooms/{code}/start", json={"player_id": host})
    client.post(f"/api/rooms/{code}/leave", json={"player_id": host})
    room = server.ROOMS[code]
    game = room["game"]
    for _ in range(400):
        if room["status"] != "playing":
            break
        server.submit_cpu_cards(room)
        if game["resolution_queue"] or game.get("pending_choice"):
            break
        if all(p["id"] in game["submissions"] for p in room["players"] if game["hands"].get(p["id"])):
            server.resolve_online_turn(room)
        else:
            break
    if room["status"] != "finished":
        # Only "cows" can stall: the last cards in circulation may share
        # no color with any lane tail, so every played card goes under a
        # deck forever. The official variation has no rule for this
        # stalemate, so accept it only when no placement is possible.
        assert variation == "cows"
        circulating = [c for h in game["hands"].values() for c in h]
        circulating += [c for d in game["decks"].values() for c in d]
        circulating += list(game["submissions"].values())
        assert circulating
        assert all(not server.matching_lanes(game["lanes"], c) for c in circulating)
    else:
        assert game["winner"]

    def all_cards():
        cards = [c for lane in game["lanes"] for c in lane]
        cards += [c for t in game["taken"].values() for c in t]
        cards += [c for h in game["hands"].values() for c in h]
        cards += [c for d in game["decks"].values() for c in d]
        cards += list(game["submissions"].values())
        cards += game["unused"]
        return sorted(c["n"] for c in cards)

    assert len(all_cards()) == 110
    assert len(set(all_cards())) == len(all_cards())
