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

def public_room(room):
    return {
        "code": room["code"],
        "host_id": room["host_id"],
        "host_name": room["host_name"],
        "max_players": room["max_players"],
        "players": [{"id": p["id"], "name": p["name"], "is_host": p["id"] == room["host_id"], "is_cpu": p.get("is_cpu", False), "is_cpu": p.get("is_cpu", False)} for p in room["players"]],
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
    with ROOM_LOCK:
        clean_rooms()
        if len(ROOMS) >= MAX_ROOMS:
            return jsonify(error="現在ルームが満室です。時間をおいてお試しください。"), 503
        code = room_code()
        player_id = secrets.token_urlsafe(18)
        now = time.time()
        room = {
            "code": code, "host_id": player_id, "host_name": name,
            "max_players": max_players,
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
                lane_index = min(
                    range(5),
                    key=lambda i: (
                        sum(c["penalty"] for c in game["lanes"][i]),
                        len(game["lanes"][i])
                    )
                )
                card = pending["card"]
                game["taken"][player_id].extend(game["lanes"][lane_index])
                game["lanes"][lane_index] = [card]
                game["last_log"].append(player["name"] + "：CPUが" + str(lane_index + 1) + "列目を選んで引き取り")
                game["pending_choice"] = None
                if game["resolution_queue"]:
                    game["resolution_queue"].pop(0)
                continue_online_resolution(room)

            if room.get("status") == "playing":
                submit_cpu_cards(room)
                if not game.get("pending_choice") and len(game["submissions"]) == len(room["players"]):
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

def game_public_state(room, player_id):
    game = room.get("game")
    if not game:
        return None
    me = next((p for p in room["players"] if p["id"] == player_id), None)
    if not me:
        return None
    pending = game.get("pending_choice")
    pending_lanes = [i for i, lane in enumerate(game["lanes"]) if pending and pending["player_id"] == player_id and pending.get("mode") == "place" and set(lane[-1]["cs"]) & set(pending["card"]["cs"])]
    return {
        "turn": game["turn"],
        "players": [{"id": p["id"], "name": p["name"], "is_host": p["id"] == room["host_id"],
                     "hand_count": len(game["hands"].get(p["id"], [])),
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
        "revealed_cards": [{"player_name": next((p["name"] for p in room["players"] if p["id"] == pid), "プレイヤー"), "card_number": card["n"]} for pid, card, _ in game.get("resolution_queue", [])],
        "pending_choice": bool(pending),
        "pending_player_name": next((p["name"] for p in room["players"] if pending and p["id"] == pending["player_id"]), None)
    }

def start_baron_game(room):
    cards = load_baron_cards()
    import random
    random.SystemRandom().shuffle(cards)
    count = len(room["players"])
    hand_size = {2:24, 3:20, 4:16, 5:16, 6:16, 7:12, 8:12, 9:8, 10:8}[count]
    hands = {}
    for p in room["players"]:
        hands[p["id"]] = sorted(cards[:hand_size], key=lambda c: c["n"])
        del cards[:hand_size]
    lanes = [[cards.pop()] for _ in range(5)]
    room["game"] = {"turn": 0, "hands": hands, "lanes": lanes,
                    "taken": {p["id"]: [] for p in room["players"]},
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
        eligible = [lane for lane in game["lanes"] if set(lane[-1]["cs"]) & set(card["cs"])]
        if not eligible:
            return 100 + min(sum(c["penalty"] for c in lane) for lane in game["lanes"])
        lane = min(eligible, key=lambda x: (len(x), sum(c["penalty"] for c in x)))
        return (30 if len(lane) >= 5 else 0) + sum(c["penalty"] for c in lane) + len(lane) * 0.4 + card["penalty"] * 0.15
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
            eligible = [i for i, lane in enumerate(game["lanes"]) if set(lane[-1]["cs"]) & set(card["cs"])]
            game.setdefault("submission_lanes", {})[player["id"]] = min(eligible, key=lambda i: (len(game["lanes"][i]), sum(c["penalty"] for c in game["lanes"][i]))) if eligible else None

def finish_online_game_if_needed(room):
    game = room["game"]
    if all(not game["hands"].get(p["id"]) for p in room["players"]):
        scores = {p["id"]: sum(c["penalty"] for c in game["taken"][p["id"]]) for p in room["players"]}
        best = min(scores.values())
        game["winner"] = [p["name"] for p in room["players"] if scores[p["id"]] == best]
        room["status"] = "finished"

def continue_online_resolution(room):
    game = room["game"]
    while game["resolution_queue"]:
        pid, card, _requested_lane = game["resolution_queue"][0]
        player = next(p for p in room["players"] if p["id"] == pid)
        eligible = [i for i, lane in enumerate(game["lanes"]) if set(lane[-1]["cs"]) & set(card["cs"])]
        if not player.get("is_cpu"):
            game["pending_choice"] = {"player_id": pid, "card": card, "mode": "place" if eligible else "take"}
            return
        if not eligible:
            chosen = min(range(5), key=lambda i: (sum(c["penalty"] for c in game["lanes"][i]), len(game["lanes"][i])))
            game["taken"][pid].extend(game["lanes"][chosen]); game["lanes"][chosen] = [card]
            game["last_log"].append(player["name"] + "：置ける列がないため" + str(chosen + 1) + "列目を引き取り")
            game["resolution_queue"].pop(0); continue
        chosen = min(eligible, key=lambda i: (len(game["lanes"][i]), sum(c["penalty"] for c in game["lanes"][i])))
        lane = game["lanes"][chosen]
        if len(lane) >= 5:
            game["taken"][pid].extend(lane); game["lanes"][chosen] = [card]
            game["last_log"].append(player["name"] + "：" + str(chosen + 1) + "列目を引き取り（6枚目）")
            game["resolution_queue"].pop(0); continue
        lane.append(card)
        color_totals = {color: sum(c["cs"].count(color) for c in lane) for color in ("red", "blue", "green", "yellow", "black", "white")}
        if max(color_totals.values(), default=0) >= 6:
            game["taken"][pid].extend(lane[:-1]); game["lanes"][chosen] = [card]
            game["last_log"].append(player["name"] + "：" + str(chosen + 1) + "列目を引き取り（同色6つ以上）")
        else: game["last_log"].append(player["name"] + "：" + str(card["n"]) + "を" + str(chosen + 1) + "列目へ配置")
        game["resolution_queue"].pop(0)
    game["pending_choice"] = None
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
        if pending.get("mode") == "place":
            eligible = [i for i, lane in enumerate(game["lanes"]) if set(lane[-1]["cs"]) & set(card["cs"])]
            if lane_index not in eligible: return jsonify(error="その列には配置できません。"), 400
            lane = game["lanes"][lane_index]
            if len(lane) >= 5:
                game["taken"][player_id].extend(lane); game["lanes"][lane_index] = [card]
                game["last_log"].append(player["name"] + "：" + str(lane_index + 1) + "列目を引き取り（6枚目）")
            else:
                lane.append(card)
                totals = {color: sum(c["cs"].count(color) for c in lane) for color in ("red", "blue", "green", "yellow", "black", "white")}
                if max(totals.values(), default=0) >= 6:
                    game["taken"][player_id].extend(lane[:-1]); game["lanes"][lane_index] = [card]
                    game["last_log"].append(player["name"] + "：" + str(lane_index + 1) + "列目を引き取り（同色6つ以上）")
                else: game["last_log"].append(player["name"] + "：" + str(card["n"]) + "を" + str(lane_index + 1) + "列目へ配置")
        else:
            game["taken"][player_id].extend(game["lanes"][lane_index]); game["lanes"][lane_index] = [card]
            game["last_log"].append(player["name"] + "：置ける列がないため" + str(lane_index + 1) + "列目を選んで引き取り")
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
        if len(game["submissions"]) == len(room["players"]):
            resolve_online_turn(room)
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
