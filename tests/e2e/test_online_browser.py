"""Two-player browser test for the online game (Playwright + real Flask server).

Skipped when Playwright is not installed. To run locally:

    pip install -r requirements-e2e.txt
    python -m playwright install chromium
    pytest tests/e2e

Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to use an existing Chromium binary instead.
"""
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server  # noqa: E402

import app as server  # noqa: E402

expect = sync_api.expect
TIMEOUT = 15_000  # the page polls every ~2 s


@pytest.fixture(scope="module")
def base_url():
    httpd = make_server("127.0.0.1", 0, server.app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


@pytest.fixture(autouse=True)
def clean_rooms():
    server.ROOMS.clear()
    yield
    server.ROOMS.clear()


def new_page(browser, base_url):
    # Separate contexts = separate localStorage sessions, like two devices.
    context = browser.new_context()
    context.set_default_timeout(TIMEOUT)
    context.route("**/cdnjs.cloudflare.com/**", lambda route: route.abort())  # QR library
    page = context.new_page()
    page.goto(base_url + "/baron-nimmt/online")
    return page


def card(n, colors, penalty=1):
    return {"n": n, "penalty": penalty, "cs": list(colors)}


def setup_room(browser, base_url):
    alice = new_page(browser, base_url)
    alice.fill("#createName", "Alice")
    alice.select_option("#maxPlayers", "2")
    alice.click("#createBtn")
    expect(alice.locator("#lobby")).to_be_visible()
    code = alice.locator("#roomCode").inner_text().strip()
    assert code in server.ROOMS

    bob = new_page(browser, base_url)
    bob.click("#joinTab")
    bob.fill("#joinName", "Bob")
    bob.fill("#joinCode", code)
    bob.click("#joinBtn")
    expect(bob.locator("#lobby")).to_be_visible()
    expect(alice.locator("#memberCount")).to_have_text("2 / 2人")
    return alice, bob, code


def test_lobby_leave_frees_seat(browser, base_url):
    alice, bob, code = setup_room(browser, base_url)
    bob.click("#leaveBtn")
    expect(alice.locator("#memberCount")).to_have_text("1 / 2人")
    room = server.ROOMS[code]
    assert [p["name"] for p in room["players"]] == ["Alice"]


def test_two_player_game_flow(browser, base_url):
    alice, bob, code = setup_room(browser, base_url)
    alice.click("#startBtn")
    expect(alice.locator("#gamePanel")).to_be_visible()
    expect(bob.locator("#gamePanel")).to_be_visible()

    # Make the deal deterministic: every lane ends in red, blue cards force a choice.
    room = server.ROOMS[code]
    ids = {p["name"]: p["id"] for p in room["players"]}
    with server.ROOM_LOCK:
        game = room["game"]
        game["lanes"] = [[card(100 + i, ["red"])] for i in range(5)]
        game["hands"][ids["Alice"]] = [card(1, ["blue"]), card(3, ["red"])]
        game["hands"][ids["Bob"]] = [card(2, ["blue"]), card(4, ["red"])]
    alice.reload()
    bob.reload()
    expect(alice.locator("#onlineHand button")).to_have_count(2)
    expect(bob.locator("#onlineHand button")).to_have_count(2)

    # Alice submits, cancels, and submits again.
    alice.click('#onlineHand button[data-n="1"]')
    alice.click("#submitCardBtn")
    cancel = alice.locator("#cancelCardBtn")
    expect(cancel).to_be_visible()
    cancel.click()
    expect(alice.locator("#onlineHand button[data-n='1']")).to_be_enabled()
    assert ids["Alice"] not in server.ROOMS[code]["game"]["submissions"]
    alice.click('#onlineHand button[data-n="1"]')
    alice.click("#submitCardBtn")
    expect(cancel).to_be_visible()

    # Bob submits; both cards are revealed and Alice (card 1) must take a lane.
    bob.click('#onlineHand button[data-n="2"]')
    bob.click("#submitCardBtn")
    take_first = alice.locator('#laneChoice button[data-lane="0"]')
    expect(take_first).to_be_visible()

    # While Alice is choosing, Bob cannot submit his next card.
    expect(bob.locator("#laneChoice")).to_contain_text("Alice")
    bob.click('#onlineHand button[data-n="4"]')
    expect(bob.locator("#submitCardBtn")).to_be_disabled()

    take_first.click()

    # Lane 1 now ends with Alice's blue card, so Bob places card 2 there.
    target = bob.locator('#onlineBoard .lane.target[data-target-lane="0"]')
    expect(target).to_be_visible()
    target.click()
    # Wait until the placement round-trip has finished before reading server state.
    expect(bob.locator('#onlineBoard .lane[data-target-lane="0"] h4')).to_have_text("1列目 · 2枚")

    with server.ROOM_LOCK:
        game = server.ROOMS[code]["game"]
        assert [c["n"] for c in game["taken"][ids["Alice"]]] == [100]
        assert [c["n"] for c in game["lanes"][0]] == [1, 2]
        assert game["pending_choice"] is None

    # Last turn: both play red cards and choose a red-ended lane themselves.
    alice.click('#onlineHand button[data-n="3"]')
    alice.click("#submitCardBtn")
    bob.click('#onlineHand button[data-n="4"]')
    expect(bob.locator("#submitCardBtn")).to_be_enabled()
    bob.click("#submitCardBtn")

    alice.locator('#onlineBoard .lane.target[data-target-lane="1"]').click()
    bob.locator('#onlineBoard .lane.target[data-target-lane="2"]').click()

    expect(alice.locator("#onlineResult")).to_be_visible()
    expect(bob.locator("#onlineResult")).to_be_visible()
    assert server.ROOMS[code]["status"] == "finished"
    assert server.ROOMS[code]["game"]["winner"] == ["Bob"]
