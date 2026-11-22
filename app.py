import hmac
import os
import secrets
import string
import threading
import time
from flask import jsonify, request
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, abort, make_response, send_from_directory

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
CONTENT_DIR = os.path.join(os.path.dirname(__file__), "baron-nimmt")

# Simple in-process room store. Render service is configured as a single instance.
ROOMS = {}
ROOM_LOCK = threading.RLock()
ROOM_TTL = 6 * 60 * 60
MAX_ROOMS = 100

def clean_rooms():
    now = time.time()
    expired = [code for code, room in ROOMS.items() if now - room["updated"] > ROOM_TTL]
    for code in expired:
        ROOMS.pop(code, None)

VARIATIONS = ("none", "color", "cows")

def public_room(room):
    return {
        "code": room["code"],
        "host_id": room["host_id"],
        "host_name": room["host_name"],
        "max_players": room["max_players"],
        "variation": room.get("variation", "none"),
        "players": [{"id": p["id"], "name": p["name"], "is_host": p["id"] == room["host_id"], "is_cpu": p.get("is_cpu", False)} for p in room["players"]],
        "status": room["status"],
        "created": room["created"]
    }

def room_code():
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(30):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in ROOMS:
            return code
    raise RuntimeError("room code generation failed")


def is_public_now():
    start = os.getenv("PUBLIC_START_JST", "").strip()
    end = os.getenv("PUBLIC_END_JST", "").strip()
    if not start or not end:
        return False
    try:
        start_at = datetime.fromisoformat(start).replace(tzinfo=JST)
        end_at = datetime.fromisoformat(end).replace(tzinfo=JST)
    except ValueError:
        return False
    now = datetime.now(JST)
    return start_at < end_at and start_at <= now < end_at


@app.before_request
def enforce_public_window():
    # Set PUBLIC_WINDOW_ENABLED=true to re-enable the scheduled public period.
    # Until then, the site remains publicly accessible.
    if os.getenv("PUBLIC_WINDOW_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    # Secret preview URLs are checked separately in the preview route.
    if request_path_is_preview():
        return None
    if not is_public_now():
        abort(404)


def request_path_is_preview():
    from flask import request
    return request.path.startswith("/preview/")


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/")
def home():
    return send_from_directory(CONTENT_DIR, "home.html")


@app.get("/catan")
def catan_tutorial():
    return send_from_directory(CONTENT_DIR, "catan.html")


@app.get("/carcassonne")
def carcassonne_quiz():
    return send_from_directory(CONTENT_DIR, "carcassonne.html")


@app.get("/acquire")
def acquire_tutorial():
    return send_from_directory(CONTENT_DIR, "acquire.html")


@app.get("/baron-nimmt")
def tutorial():
    return send_from_directory(CONTENT_DIR, "home.html")


@app.get("/baron-nimmt/online")
def online_game():
    return send_from_directory(CONTENT_DIR, "online.html")


@app.post("/api/rooms")
def create_room():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:16]
    try:
        max_players = int(data.get("max_players", 4))
    except (TypeError, ValueError):
        return jsonify(error="参加人数が正しくありません。"), 400
    if not name:
        return jsonify(error="プレイヤー名を入力してください。"), 400
    if not 2 <= max_players <= 10:
        return jsonify(error="参加人数は2〜10人にしてください。"), 400
    variation = str(data.get("variation", "none"))
    if variation not in VARIATIONS:
        return jsonify(error="バリエーションの指定が正しくありません。"), 400
    with ROOM_LOCK:
        clean_rooms()
        if len(ROOMS) >= MAX_ROOMS:
            return jsonify(error="現在ルームが満室です。時間をおいてお試しください。"), 503
        code = room_code()
        player_id = secrets.token_urlsafe(18)
        now = time.time()
        room = {
            "code": code, "host_id": player_id, "host_name": name,
            "max_players": max_players, "variation": variation,
            "players": [{"id": player_id, "name": name, "joined": now}],
            "status": "waiting", "created": now, "updated": now
        }
        ROOMS[code] = room
        return jsonify(room=public_room(room), player_id=player_id)


@app.post("/api/rooms/<code>/join")
def join_room(code):
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:16]
    if not name:
        return jsonify(error="プレイヤー名を入力してください。"), 400
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが見つかりません。番号を確認してください。"), 404
        if room["status"] != "waiting":
            return jsonify(error="このルームでは募集を終了しています。"), 409
        if len(room["players"]) >= room["max_players"]:
            return jsonify(error="このルームは満員です。"), 409
        player_id = secrets.token_urlsafe(18)
        room["players"].append({"id": player_id, "name": name, "joined": time.time()})
        host = next((p for p in room["players"] if p["id"] == room["host_id"]), None)
        if host and host.get("is_cpu"):
            room["host_id"] = player_id
            room["host_name"] = name
        room["updated"] = time.time()
        return jsonify(room=public_room(room), player_id=player_id)


@app.get("/api/rooms/<code>")
def get_room(code):
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが終了したか、見つかりません。"), 404
        room["updated"] = time.time()
        return jsonify(room=public_room(room))


@app.post("/api/rooms/<code>/leave")
def leave_room(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    with ROOM_LOCK:
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(ok=True)
        player = next((p for p in room["players"] if p["id"] == player_id), None)
        if not player:
            return jsonify(ok=True)

        # Before the game starts, free the seat instead of filling it with a CPU.
        if room["status"] == "waiting":
            room["players"].remove(player)
            humans = [p for p in room["players"] if not p.get("is_cpu")]
            if not humans:
                ROOMS.pop(room["code"], None)
                return jsonify(ok=True, room_closed=True)
            if player_id == room["host_id"]:
                room["host_id"] = humans[0]["id"]
                room["host_name"] = humans[0]["name"]
            room["updated"] = time.time()
            return jsonify(ok=True)

        # Keep the seat and game state, but hand control to a CPU.
        cpu_number = 1 + sum(1 for p in room["players"] if p.get("is_cpu"))
        player["name"] = "CPU " + str(cpu_number)
        player["is_cpu"] = True
        player["joined"] = time.time()

        # If the host leaves, transfer host privileges to a remaining human.
        if player_id == room["host_id"]:
            next_host = next((p for p in room["players"] if not p.get("is_cpu")), None)
            room["host_id"] = next_host["id"] if next_host else player_id
            room["host_name"] = next_host["name"] if next_host else player["name"]

        game = room.get("game")
        if game and room.get("status") == "playing":
            pending = game.get("pending_choice")
            if pending and pending["player_id"] == player_id:
                # Resolve an outstanding lane choice automatically for the new CPU.
                card = pending["card"]
                play_card_to_lane(game, player, card, cpu_lane_choice(game["lanes"], card))
                game["pending_choice"] = None
                if game["resolution_queue"]:
                    game["resolution_queue"].pop(0)
                continue_online_resolution(room)

            if room.get("status") == "playing":
                submit_cpu_cards(room)
                if not game.get("pending_choice") and all(p["id"] in game["submissions"] for p in room["players"] if game["hands"].get(p["id"])):
                    resolve_online_turn(room)

        room["updated"] = time.time()
        return jsonify(ok=True, replaced_by_cpu=True)


@app.post("/api/rooms/<code>/start")
def start_room(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    with ROOM_LOCK:
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが見つかりません。"), 404
        if player_id != room["host_id"]:
            return jsonify(error="開始できるのはルーム作成者です。"), 403
        if room["status"] != "waiting":
            return jsonify(error="このルームは開始済みです。"), 409
        while len(room["players"]) < room["max_players"]:
            cpu_number = 1 + sum(1 for p in room["players"] if p.get("is_cpu"))
            room["players"].append({"id": "cpu_" + secrets.token_urlsafe(10), "name": "CPU " + str(cpu_number), "joined": time.time(), "is_cpu": True})
        start_baron_game(room)
        room["updated"] = time.time()
        return jsonify(room=public_room(room))



def load_baron_cards():
    import json
    with open(os.path.join(CONTENT_DIR, "cards.json"), encoding="utf-8") as f:
        raw = json.load(f)
    return [{"n": row[0], "penalty": row[1], "cs": [raw["colors"][i] for i in row[2]]} for row in raw["cards"]]


# ---- Lane rules (shared by CPU resolution, human choices and leaving players) ----

def lane_penalty(lane):
    return sum(c["penalty"] for c in lane)

def matching_lanes(lanes, card):
    """Indexes of lanes whose last card shares at least one color with card."""
    return [i for i, lane in enumerate(lanes) if set(lane[-1]["cs"]) & set(card["cs"])]

def cpu_lane_choice(lanes, card):
    """Shortest/cheapest matching lane, or the cheapest lane to take if none match."""
    eligible = matching_lanes(lanes, card)
    if eligible:
        return min(eligible, key=lambda i: (len(lanes[i]), lane_penalty(lanes[i])))
    return min(range(len(lanes)), key=lambda i: (lane_penalty(lanes[i]), len(lanes[i])))

def play_card_to_lane(game, player, card, lane_index, chosen_by_player=False):
    """Apply card to lane_index and log the result.

    The caller must pass a matching lane when one exists; when no lane
    matches, lane_index is the lane the player takes. In the "cows"
    variation a card with no matching lane instead goes to the bottom
    of the player's own deck and lane_index is ignored.
    """
    lanes = game["lanes"]
    taken = game["taken"][player["id"]]
    lane = lanes[lane_index]
    label = player["name"] + "：" + str(lane_index + 1) + "列目"
    log = game["last_log"]
    variation = game.get("variation", "none")
    if not game["hands"].get(player["id"]) and not game.get("decks", {}).get(player["id"]):
        # This card was the player's last one (hand and deck are empty
        # now that it has been played). Used by the "cows" end rule.
        game["last_card_played"] = True
    if not matching_lanes(lanes, card):
        if variation == "cows":
            deck = game.setdefault("decks", {}).setdefault(player["id"], [])
            deck.insert(0, card)
            log.append(player["name"] + "：置ける列がないため、カードを自分の山札の一番下へ")
            return
        taken.extend(lane)
        lanes[lane_index] = [card]
        verb = "を選んで引き取り" if chosen_by_player else "を引き取り"
        log.append(player["name"] + "：置ける列がないため" + str(lane_index + 1) + "列目" + verb)
    elif len(lane) >= 5 and variation != "color":
        taken.extend(lane)
        lanes[lane_index] = [card]
        log.append(label + "を引き取り（6枚目）")
    else:
        lane.append(card)
        if any(sum(c["cs"].count(color) for c in lane) >= 6 for color in {x for c in lane for x in c["cs"]}):
            taken.extend(lane[:-1])
            lanes[lane_index] = [card]
            log.append(label + "を引き取り（同色6つ以上）")
        else:
            log.append(player["name"] + "：" + str(card["n"]) + "を" + str(lane_index + 1) + "列目へ配置")

def game_public_state(room, player_id):
    game = room.get("game")
    if not game:
        return None
    me = next((p for p in room["players"] if p["id"] == player_id), None)
    if not me:
        return None
    pending = game.get("pending_choice")
    pending_lanes = matching_lanes(game["lanes"], pending["card"]) if pending and pending["player_id"] == player_id and pending.get("mode") == "place" else []
    return {
        "turn": game["turn"],
        "variation": game.get("variation", "none"),
        "players": [{"id": p["id"], "name": p["name"], "is_host": p["id"] == room["host_id"],
                     "hand_count": len(game["hands"].get(p["id"], [])),
                     "deck_count": len(game.get("decks", {}).get(p["id"], [])),
                     "taken_count": len(game["taken"].get(p["id"], [])),
                     "score": sum(c["penalty"] for c in game["taken"].get(p["id"], [])),
                     "submitted": p["id"] in game["submissions"]} for p in room["players"]],
        "lanes": game["lanes"],
        "my_hand": game["hands"].get(player_id, []),
        "my_submitted": player_id in game["submissions"],
        "submitted_count": len(game["submissions"]),
        "status": room["status"],
        "last_log": game.get("last_log", []),
        "winner": game.get("winner"),
        "must_choose_lane": bool(pending and pending["player_id"] == player_id),
        "pending_choice_mode": pending.get("mode") if pending and pending["player_id"] == player_id else None,
        "pending_lanes": pending_lanes,
        "pending_card_number": pending["card"]["n"] if pending and pending["player_id"] == player_id else None,
        "pending_card": pending["card"] if pending and pending["player_id"] == player_id else None,
        "revealed_cards": [{"player_name": next((p["name"] for p in room["players"] if p["id"] == pid), "プレイヤー"), "card_number": card["n"]} for pid, card, _ in game.get("resolution_queue", [])],
        "pending_choice": bool(pending),
        "pending_player_name": next((p["name"] for p in room["players"] if pending and p["id"] == pending["player_id"]), None)
    }

def deck_size_for_players(count):
    """Cards dealt to each player as their personal deck, by player count:
    2 players: 24, 3: 20, 4-6: 16, 7-8: 12, 9-10: 8."""
    if count <= 2:
        return 24
    if count == 3:
        return 20
    if count <= 6:
        return 16
    if count <= 8:
        return 12
    return 8

def start_baron_game(room):
    cards = load_baron_cards()
    import random
    random.SystemRandom().shuffle(cards)
    size = deck_size_for_players(len(room["players"]))
    hands = {}
    decks = {}
    for p in room["players"]:
        own = cards[:size]
        del cards[:size]
        hands[p["id"]] = sorted(own[:4], key=lambda c: c["n"])
        decks[p["id"]] = own[4:]
    lanes = [[cards.pop()] for _ in range(5)]
    room["game"] = {"turn": 0, "hands": hands, "lanes": lanes, "decks": decks,
                    "unused": cards, "variation": room.get("variation", "none"),
                    "taken": {p["id"]: [] for p in room["players"]},
                    "last_card_played": False,
                    "submissions": {}, "submission_lanes": {}, "last_log": [], "winner": None,
                    "resolution_queue": [], "pending_choice": None}
    room["status"] = "playing"
    submit_cpu_cards(room)

def choose_cpu_online_card(room, player):
    game = room["game"]
    hand = game["hands"].get(player["id"], [])
    if not hand:
        return None
    def value(card):
        lanes = game["lanes"]
        if not matching_lanes(lanes, card):
            return 100 + min(lane_penalty(lane) for lane in lanes)
        lane = lanes[cpu_lane_choice(lanes, card)]
        return (30 if len(lane) >= 5 else 0) + lane_penalty(lane) + len(lane) * 0.4 + card["penalty"] * 0.15
    return min(hand, key=value)

def submit_cpu_cards(room):
    game = room.get("game")
    if not game or room.get("status") != "playing" or game.get("pending_choice"):
        return
    for player in room["players"]:
        if not player.get("is_cpu") or player["id"] in game["submissions"]:
            continue
        card = choose_cpu_online_card(room, player)
        if card is not None:
            game["hands"][player["id"]].remove(card)
            game["submissions"][player["id"]] = card

def draw_online_hands(room):
    """Official refill rule: hands are NOT refilled at the end of a
    round. Only a player who has played out their whole hand draws
    4 new cards from their own deck, at the end of that round."""
    game = room["game"]
    decks = game.get("decks") or {}
    for p in room["players"]:
        hand = game["hands"].setdefault(p["id"], [])
        if hand:
            continue
        own = decks.get(p["id"]) or []
        while len(hand) < 4 and own:
            hand.append(own.pop())
        hand.sort(key=lambda c: c["n"])

def finish_online_game_if_needed(room):
    game = room["game"]
    decks = game.get("decks") or {}
    out = {p["id"]: not game["hands"].get(p["id"]) and not decks.get(p["id"]) for p in room["players"]}
    if game.get("variation", "none") == "cows":
        # "Gather the cows": the game ends after the round in which at
        # least one player played their last card; most marks win. (A
        # card that went under the deck still counts as the last card
        # having been played, so the flag -- not just the empty state --
        # decides; otherwise such a card could cycle forever.)
        finished = bool(game.get("last_card_played")) or any(out.values())
    else:
        finished = all(out.values())
    if finished:
        scores = {p["id"]: sum(c["penalty"] for c in game["taken"][p["id"]]) for p in room["players"]}
        best = max(scores.values()) if game.get("variation", "none") == "cows" else min(scores.values())
        game["winner"] = [p["name"] for p in room["players"] if scores[p["id"]] == best]
        room["status"] = "finished"

def continue_online_resolution(room):
    game = room["game"]
    while game["resolution_queue"]:
        pid, card, _requested_lane = game["resolution_queue"][0]
        player = next(p for p in room["players"] if p["id"] == pid)
        if not player.get("is_cpu"):
            if game.get("variation", "none") == "cows" and not matching_lanes(game["lanes"], card):
                # No lane choice to make: the card goes under the deck.
                play_card_to_lane(game, player, card, 0)
                game["resolution_queue"].pop(0)
                continue
            mode = "place" if matching_lanes(game["lanes"], card) else "take"
            game["pending_choice"] = {"player_id": pid, "card": card, "mode": mode}
            return
        play_card_to_lane(game, player, card, cpu_lane_choice(game["lanes"], card))
        game["resolution_queue"].pop(0)
    game["pending_choice"] = None
    draw_online_hands(room)
    finish_online_game_if_needed(room)

def resolve_online_turn(room):
    game = room["game"]
    game["resolution_queue"] = sorted(
        [(pid, card, game.get("submission_lanes", {}).get(pid)) for pid, card in game["submissions"].items()],
        key=lambda item: item[1]["n"]
    )
    game["submissions"] = {}
    game["submission_lanes"] = {}
    game["last_log"] = []
    game["turn"] += 1
    continue_online_resolution(room)
    if room["status"] == "playing":
        submit_cpu_cards(room)
    game["last_log"] = game["last_log"][-20:]
    room["updated"] = time.time()

@app.post("/api/rooms/<code>/choose-lane")
def choose_online_lane(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    try: lane_index = int(data.get("lane_index"))
    except (TypeError, ValueError): return jsonify(error="列を選択してください。"), 400
    with ROOM_LOCK:
        room = ROOMS.get(code.upper())
        if not room or room.get("status") != "playing": return jsonify(error="ゲームが見つかりません。"), 404
        game = room["game"]; pending = game.get("pending_choice")
        if not pending or pending["player_id"] != player_id: return jsonify(error="列を選択する順番ではありません。"), 403
        if not 0 <= lane_index < 5: return jsonify(error="列の指定が正しくありません。"), 400
        card = pending["card"]; player = next(p for p in room["players"] if p["id"] == player_id)
        eligible = matching_lanes(game["lanes"], card)
        if eligible and lane_index not in eligible:
            return jsonify(error="その列には配置できません。"), 400
        play_card_to_lane(game, player, card, lane_index, chosen_by_player=True)
        game["pending_choice"] = None
        if game["resolution_queue"]: game["resolution_queue"].pop(0)
        continue_online_resolution(room)
        if room.get("status") == "playing" and not game.get("pending_choice"): submit_cpu_cards(room)
        game["last_log"] = game["last_log"][-20:]; room["updated"] = time.time()
        return jsonify(game=game_public_state(room, player_id))

@app.get("/api/rooms/<code>/game")
def get_online_game(code):
    player_id = request.args.get("player_id", "")
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room:
            return jsonify(error="ルームが見つかりません。"), 404
        state = game_public_state(room, player_id)
        if state is None:
            return jsonify(error="参加者情報を確認できません。"), 403
        return jsonify(game=state)

@app.post("/api/rooms/<code>/play")
def submit_online_card(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    try:
        card_number = int(data.get("card_number"))
    except (TypeError, ValueError):
        return jsonify(error="カードを選択してください。"), 400
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room or room.get("status") not in ("playing",):
            return jsonify(error="現在プレイできるゲームがありません。"), 409
        game = room.get("game", {})
        hand = game.get("hands", {}).get(player_id)
        if hand is None:
            return jsonify(error="参加者情報を確認できません。"), 403
        if game.get("pending_choice") or game.get("resolution_queue"):
            return jsonify(error="前のターンのカードを処理中です。処理が終わるまでお待ちください。"), 409
        if player_id in game["submissions"]:
            return jsonify(error="このターンはすでにカードを提出しています。"), 409
        card = next((c for c in hand if c["n"] == card_number), None)
        if card is None:
            return jsonify(error="そのカードは手札にありません。"), 400
        # Lane selection is deferred until this card reaches its resolution turn.
        hand.remove(card)
        game["submissions"][player_id] = card
        game.setdefault("submission_lanes", {})[player_id] = None
        room["updated"] = time.time()
        if all(p["id"] in game["submissions"] for p in room["players"] if game["hands"].get(p["id"])):
            resolve_online_turn(room)
        return jsonify(game=game_public_state(room, player_id))

@app.post("/api/rooms/<code>/cancel")
def cancel_online_submission(code):
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id", ""))
    with ROOM_LOCK:
        clean_rooms()
        room = ROOMS.get(code.upper())
        if not room or room.get("status") != "playing":
            return jsonify(error="現在カードを取り消せるゲームがありません。"), 409
        game = room.get("game", {})
        if player_id not in game.get("hands", {}):
            return jsonify(error="参加者情報を確認できません。"), 403
        if game.get("pending_choice") or game.get("resolution_queue"):
            return jsonify(error="カードの処理中は取り消せません。"), 409
        submissions = game.get("submissions", {})
        if player_id not in submissions:
            return jsonify(error="取り消すカードがありません。"), 409
        if len(submissions) >= len(room["players"]):
            return jsonify(error="全員のカード提出が完了したため、取り消せません。"), 409
        card = submissions.pop(player_id)
        game.setdefault("hands", {}).setdefault(player_id, []).append(card)
        game["hands"][player_id].sort(key=lambda c: c["n"])
        game.get("submission_lanes", {}).pop(player_id, None)
        room["updated"] = time.time()
        return jsonify(game=game_public_state(room, player_id))
@app.get("/baron-nimmt/cpu")
def cpu_game():
    return send_from_directory(CONTENT_DIR, "cpu.html")


@app.get("/baron-nimmt/cards.json")
def baron_cards():
    return send_from_directory(CONTENT_DIR, "cards.json", mimetype="application/json")


def valid_preview_token(token):
    expected = os.getenv("PREVIEW_URL_TOKEN", "")
    return bool(expected) and hmac.compare_digest(token, expected)


@app.get("/preview/<token>/baron-nimmt")
def preview_baron_nimmt(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "index.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/tutorial"')
    html = html.replace('href="/baron-nimmt/cpu"', 'href="/preview/' + token + '/cpu"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>/cpu")
def preview_cpu(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "cpu.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/tutorial"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>/tutorial")
def preview_tutorial(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "draft.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt/cpu"', 'href="/preview/' + token + '/cpu"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/preview/<token>")
def preview(token):
    if not valid_preview_token(token):
        abort(404)
    with open(os.path.join(CONTENT_DIR, "home.html"), encoding="utf-8") as file:
        html = file.read()
    html = html.replace('href="/baron-nimmt"', 'href="/preview/' + token + '/baron-nimmt"')
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


@app.get("/healthz")
def health():
    # Health endpoint reveals no tutorial content or event schedule.
    return make_response("ok", 200)
