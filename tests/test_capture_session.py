"""The capture session's own life, on the fake emulator of
:mod:`capture_fake`: replays stopped or failed and tried again, moments
announced badly or twice, files that do not read, playing, a game handed to a
window already open, the emulator closing, ``incoming/`` looked through while
playing, messages, and the captures' folder moving."""

from __future__ import annotations

import json
import os
import socket
import time

import pytest

from capture_fake import (
    CONSOLE,
    MESSAGES,
    FakeEmulator,
    FakeProc,
    Game,
    build_rom,
    write_moment,
)
from mapchar.capture import session as session_mod
from mapchar.capture.emulator import RECORDER, REPLAY
from mapchar.capture.evidence import EVIDENCE
from mapchar.capture.protocol import CaptureError
from mapchar.capture.session import (
    DONE,
    FAILED,
    INCOMING,
    META,
    REJECTED,
    REPLAYING,
    TRACING,
    UNREADABLE,
    WAITING,
    Session,
    close_now,
    open_root,
)
from mapchar.capture.setup import SETUP, Setup
from test_capture import arrive, make_session, run_session

WELCOME = MESSAGES[1]


@pytest.fixture
def game():
    return Game(build_rom(), message=1)


class SlowEmulator(FakeEmulator):
    """A replay that writes the start of its log, then takes ``slow``
    seconds — or until it is killed — to finish."""

    def __init__(self, game: Game, slow: float = 30.0, **kw):
        super().__init__(game, **kw)
        self.slow = slow

    def launch(self, rom, role, script, log=None):
        if role != REPLAY:
            return super().launch(rom, role, script, log)
        folder = os.path.dirname(script)

        def run(proc):
            with open(os.path.join(folder, EVIDENCE), "w") as fh:
                fh.write("F 1\n")
            with open(os.path.join(folder, EVIDENCE + ".acc"), "wb") as fh:
                fh.write(b"\x01")
            if not proc.stopped.wait(self.slow):
                FakeEmulator.launch(self, rom, role, script, log).wait()

        return FakeProc(run)


class HandingEmulator(FakeEmulator):
    """Mesen with a window already open: the launched recorder exits at
    once, and the script runs in the other window."""

    def launch(self, rom, role, script, log=None):
        if role != RECORDER:
            return super().launch(rom, role, script, log)
        self.launched.append(role)
        return FakeProc(lambda proc: None)


class Peer:
    """The recorder script's end of the connection."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port))

    def send(self, *lines: str) -> None:
        self.sock.sendall("".join(line + "\n" for line in lines).encode())

    def close(self) -> None:
        self.sock.close()


def until(session: Session, check, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        session.advance(0.02)
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def age(path: str, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def evidence_files(folder: str) -> list[str]:
    return sorted(f for f in os.listdir(folder) if f.startswith(EVIDENCE))


def moment_in_incoming(session: Session, name: str, age: float = 0.0) -> None:
    """A whole moment as the recorder leaves it in ``incoming/``, its files
    ``age`` seconds old."""
    incoming = os.path.join(session.root, INCOMING)
    tmp = os.path.join(incoming, name + "_tmp")
    write_moment(tmp)
    for f, to in (
        ("moment.txt", ".txt"),
        ("s01.mss", "_s01.mss"),
        ("input.txt", "_input.txt"),
        ("screen.png", ".png"),
    ):
        os.replace(os.path.join(tmp, f), os.path.join(incoming, name + to))
        if age:
            t = time.time() - age
            os.utime(os.path.join(incoming, name + to), (t, t))
    os.rmdir(tmp)


# -- the replay's evidence


def test_a_stopped_replay_leaves_no_evidence_and_is_run_again(tmp_path, game):
    s = make_session(tmp_path, game)
    s.emulator = SlowEmulator(game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    until(s, lambda: os.path.exists(os.path.join(cap.folder, EVIDENCE + ".acc")))
    assert cap.state == REPLAYING
    started = time.monotonic()
    s.stop()
    assert time.monotonic() - started < 3
    assert cap.state == FAILED and cap.reason == "stopped"
    assert evidence_files(cap.folder) == [] and not cap.replayed
    s.emulator = FakeEmulator(game)
    s.retry(cap)
    run_session(s)
    assert cap.state == DONE, cap.reason
    assert s.emulator.launched.count(REPLAY) == 1  # replayed again, not skipped


def test_a_replay_that_differs_leaves_no_evidence(tmp_path, game):
    s = make_session(tmp_path, game, match=False)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    run_session(s)
    assert cap.state == FAILED and "reproduce" in cap.reason
    assert evidence_files(cap.folder) == []
    # Retry replays again rather than tracing what the failed replay left.
    s.emulator.match = True
    s.retry(cap)
    run_session(s)
    assert cap.state == DONE, cap.reason
    assert s.emulator.launched.count(REPLAY) == 2


def test_evidence_is_kept_once_it_replayed_whole(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    run_session(s)
    assert cap.state == DONE and cap.replayed
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    (back,) = again.captures
    assert back.replayed
    again.retry(back)
    run_session(again)
    assert back.state == DONE
    assert s.emulator.launched.count(REPLAY) == 1


def test_a_finished_capture_keeps_no_scripts(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    run_session(s)
    left = [f for f in os.listdir(cap.folder) if f.startswith("_")]
    assert left == []


def test_evidence_left_by_an_older_version_is_replayed(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    with open(os.path.join(cap.folder, EVIDENCE), "w") as fh:
        fh.write("F 1\n")  # partial, and never said to have matched
    s.submit(cap, WELCOME)
    run_session(s)
    assert cap.state == DONE, cap.reason
    assert s.emulator.launched.count(REPLAY) == 1


# -- stopping and closing


def test_close_now_asks_for_no_wait():
    class Server:
        def close(self, wait: float = 5.0):
            self.wait = wait

    server = Server()
    close_now(server)
    assert server.wait == 0


def test_retyping_a_capture_being_traced_traces_it_again(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    until(s, lambda: cap.state == TRACING, 60)
    assert s.submit(cap, "Island of Tests") is None
    assert s.job is None and cap.state == WAITING
    run_session(s)
    assert cap.state == DONE, cap.reason
    assert s.sightings()[0].typed == "Island of Tests"
    assert cap.result.outputs <= len("Island of Tests")


def test_skipping_the_capture_being_traced_stops_it_first(tmp_path, game):
    s = make_session(tmp_path, game)
    s.emulator = SlowEmulator(game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    until(s, lambda: cap.state == REPLAYING)
    s.skip(cap)
    assert s.job is None and not s.captures
    assert not os.path.exists(cap.folder)


# -- moments


def test_a_second_moment_with_a_taken_id_gets_its_own(tmp_path, game):
    s = make_session(tmp_path, game)
    first = arrive(s, "0001")
    s.submit(first, WELCOME)
    meta = os.path.join(first.folder, META)
    with open(meta, encoding="utf-8") as fh:
        before = fh.read()
    second = arrive(s, "0001")
    assert second.id == "0001-2" and second.folder != first.folder
    assert [c.id for c in s.captures] == ["0001", "0001-2"]
    with open(meta, encoding="utf-8") as fh:
        assert fh.read() == before
    assert first.text == WELCOME and not second.text


def test_the_recorders_lines_never_raise(tmp_path, game):
    said = []
    s = make_session(tmp_path, game)
    s.on_message = said.append
    s.play()
    peer = Peer(s.listener.port)
    peer.send(
        "hello recorder",
        "moment ",
        "moment",
        "moment nothere",
        "moment ../escape",
        "moment ..",
        "early",
        "err boom went the script",
        "what is this",
        "",
    )
    until(s, lambda: len(said) >= 7)
    assert s.hello and s.recorder_state == "connected"
    assert any("what is this" in m for m in said)  # an unknown line is said
    assert not s.captures
    text = "\n".join(said)
    assert "cannot read: ''" in text and "'../escape'" in text and "'..'" in text
    assert "could not be taken in" in text  # nothere: no files
    assert "Paused too soon" in text and "boom went the script" in text
    # A moment that is there is taken in.
    moment_in_incoming(s, "0005")
    peer.send("moment 0005")
    until(s, lambda: s.capture("0005") is not None)
    peer.close()
    s.stop_playing()


def test_a_moment_taken_in_before_it_is_announced_is_not_taken_twice(tmp_path, game):
    s = make_session(tmp_path, game)
    s.play()
    peer = Peer(s.listener.port)
    peer.send("hello recorder")
    until(s, lambda: s.hello)
    moment_in_incoming(s, "0007", age=5)
    s._recovered_at = 0
    until(s, lambda: s.capture("0007") is not None)
    before = list(s.messages)
    peer.send("moment 0007")
    time.sleep(0.05)
    s.advance(0.02)
    assert [c.id for c in s.captures] == ["0007"] and s.messages == before
    peer.close()
    s.stop_playing()


# -- incoming/, looked through while playing


def test_incoming_is_looked_through_while_playing(tmp_path, game):
    s = make_session(tmp_path, game)
    s.play()
    moment_in_incoming(s, "0002")  # just written: may still be being written
    s._recovered_at = 0
    s.advance(0.01)
    assert s.capture("0002") is None
    t = time.time() - 5
    for f in os.listdir(os.path.join(s.root, INCOMING)):
        os.utime(os.path.join(s.root, INCOMING, f), (t, t))
    s._recovered_at = 0
    s.advance(0.01)
    assert s.capture("0002") is not None
    s.stop_playing()


def test_what_never_reads_is_set_aside_once(tmp_path, game, monkeypatch):
    s = make_session(tmp_path, game)
    incoming = os.path.join(s.root, INCOMING)
    for name in ("0003.txt", "0004_s01.mss", "0004.png"):
        with open(os.path.join(incoming, name), "w") as fh:
            fh.write("capture nothing\n" if name.endswith(".txt") else "x")
        age(os.path.join(incoming, name), 5)
    s._recovered_at = 0
    s.advance(0.01)
    s._recovered_at = 0
    s.advance(0.01)
    unreadable = [m for m in s.messages if "0003" in m]
    assert len(unreadable) == 1  # said once, however often it is looked at
    assert not any("0004" in m for m in s.messages)  # may be finished yet
    monkeypatch.setattr(session_mod, "ABANDON_SECONDS", 0.0)
    s._recovered_at = 0
    s.advance(0.01)
    rejected = os.path.join(incoming, REJECTED)
    assert sorted(os.listdir(rejected)) == ["0003.txt", "0004.png", "0004_s01.mss"]
    assert not [f for f in os.listdir(incoming) if f != REJECTED]
    assert any("0004" in m and REJECTED in m for m in s.messages)
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    assert not again.messages


# -- files that do not read


@pytest.mark.parametrize(
    "written",
    [
        "",
        '{"text": "Hello th',
        "[1, 2]",
        json.dumps({"text": "Hello there.", "finding": {"kind": "x", "nope": 1}}),
        json.dumps({"text": "Hello there.", "result": {"sources": [{"bad": 1}]}}),
        json.dumps({"text": "Hello there.", "order": "first"}),
    ],
)
def test_a_capture_whose_file_does_not_read_is_kept_failed(tmp_path, game, written):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    with open(os.path.join(cap.folder, META), "w") as fh:
        fh.write(written)
    other = arrive(s, "0002")
    s.submit(other, WELCOME)
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    back = again.capture("0001")
    assert back.state == FAILED and back.reason == UNREADABLE
    assert back.result is None and back.finding is None
    if written.startswith('{"text": "Hello there."'):
        assert back.text == "Hello there."
    assert again.capture("0002").text == WELCOME


def test_a_finding_with_fields_this_version_lacks_reads(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    finding = {"kind": "no-match", "message": "m", "words": ["a"], "later": 1}
    with open(os.path.join(cap.folder, META), "w") as fh:
        json.dump({"text": "Hello there.", "state": FAILED, "finding": finding}, fh)
    back = Session(s.root, s.rom_path, CONSOLE, s.emulator).captures[0]
    assert back.finding.kind == "no-match" and back.finding.words == ["a"]


@pytest.mark.parametrize(
    "written", ["", "{", "[]", '{"font": {"memory": "rom"}}', '{"font": 5}']
)
def test_a_setup_that_does_not_read_is_empty(tmp_path, written):
    with open(tmp_path / SETUP, "w") as fh:
        fh.write(written)
    assert Setup.load(str(tmp_path)) == Setup()


# -- playing


def test_a_launch_that_fails_leaves_nothing_open(tmp_path, game, monkeypatch):
    s = make_session(tmp_path, game)

    def fail(*a, **k):
        raise OSError("no such emulator")

    monkeypatch.setattr(s.emulator, "launch", fail)
    with pytest.raises(OSError):
        s.play()
    assert s.listener is None and s.player is None and not s.playing
    monkeypatch.undo()
    s.play()
    first = s.recorder_script
    assert s.playing and s.listener is not None
    s.stop_playing()
    s.play()
    assert s.recorder_script != first and not os.path.exists(first)
    s.stop_playing()


def test_play_without_an_emulator_says_so(tmp_path, game):
    s = make_session(tmp_path, game)
    s.emulator = None
    with pytest.raises(CaptureError):
        s.play()
    cap = arrive(s)
    s.submit(cap, WELCOME)
    assert s.needs_emulator and not s.busy
    assert not s.advance(0.01) and cap.state == WAITING


def test_a_game_handed_to_an_open_window_is_played_there(tmp_path, game):
    s = make_session(tmp_path, game)
    s.emulator = HandingEmulator(game)
    s.play()
    peer = Peer(s.listener.port)
    peer.send("hello recorder")
    until(s, lambda: s.handed_over)
    assert s.playing and s.recorder_state == "connected"
    assert any("already open" in m for m in s.messages)
    peer.close()
    until(s, lambda: s.listener is None)
    assert not s.playing and s.recorder_state == "closed"
    assert s.messages[-1] == "The emulator closed."


def test_a_recorder_that_never_says_hello_is_said(tmp_path, game, monkeypatch):
    monkeypatch.setattr(session_mod, "START_SECONDS", 0.05)
    s = make_session(tmp_path, game)
    s.play()
    time.sleep(0.1)
    s.advance(0.01)
    assert any("did not start" in m for m in s.messages)
    assert s.playing  # the emulator is still up: the user may yet fix it
    s.stop_playing()


def test_the_emulator_closing_ends_playing(tmp_path, game, monkeypatch):
    monkeypatch.setattr(session_mod, "HANDOFF_SECONDS", 0.0)
    s = make_session(tmp_path, game)
    s.play()
    peer = Peer(s.listener.port)
    peer.send("hello recorder")
    until(s, lambda: s.hello)
    time.sleep(0.01)
    s.player.kill()  # the user closes it
    peer.close()
    until(s, lambda: s.listener is None)
    assert s.player is None and not s.playing and s.recorder_state == "closed"
    assert "The emulator closed." in s.messages


# -- messages


def test_messages_are_said_at_once_and_age_out(tmp_path, game):
    said = []
    s = make_session(tmp_path, game)
    s.on_message = said.append
    arrive(s, "0001", "font watched\n")
    assert said and "font was not read" in said[-1]
    assert s.recent_messages() == said[-1:]
    assert s.recent_messages(seconds=0) == []


# -- the captures' folder


def test_captures_made_before_the_project_was_saved_are_found(tmp_path, game):
    s = make_session(tmp_path, game)
    arrive(s)
    project = str(tmp_path / "game.mapchar")
    assert open_root(project, s.rom_path) == s.root  # the ROM's, meanwhile
    root = project + ".capture"
    assert s.rehome(root)
    assert s.root == root and not os.path.exists(str(tmp_path / "game.bin.capture"))
    assert s.captures[0].folder == os.path.join(root, "0001")
    assert open_root(project, s.rom_path) == root
    again = Session(root, s.rom_path, CONSOLE, s.emulator)
    assert [c.id for c in again.captures] == ["0001"]
    # Somewhere already taken is left alone.
    os.makedirs(tmp_path / "other.mapchar.capture")
    assert not s.rehome(str(tmp_path / "other.mapchar.capture"))


def test_captures_do_not_move_while_the_game_is_played(tmp_path, game):
    s = make_session(tmp_path, game)
    s.play()
    assert not s.rehome(str(tmp_path / "game.mapchar.capture"))
    s.stop_playing()


# -- review findings


def test_a_moment_still_being_written_waits_even_when_not_listening(tmp_path, game):
    s = make_session(tmp_path, game)
    moment_in_incoming(s, "0009")  # just written, by a recorder left running
    s.stop_playing()  # not listening
    assert s.capture("0009") is None
    for f in os.listdir(os.path.join(s.root, INCOMING)):
        age(os.path.join(s.root, INCOMING, f), 5)
    s._recovered_at = 0
    s.advance(0.01)
    assert s.capture("0009") is not None


def test_a_description_that_is_not_text_is_said(tmp_path, game):
    s = make_session(tmp_path, game)
    s.play()
    peer = Peer(s.listener.port)
    junk = os.path.join(s.root, INCOMING, "junk.txt")
    with open(junk, "wb") as fh:
        fh.write(b"\xff\xfe\x00bad")
    age(junk, 5)
    s._recovered_at = 0
    s.advance(0.01)  # looked through: said, never raised
    assert any("junk" in m for m in s.messages)
    peer.send("moment junk")
    until(s, lambda: any("could not be taken in" in m for m in s.messages))
    peer.close()
    s.stop_playing()
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)  # opens
    assert not again.captures


def test_a_move_that_fails_keeps_the_folder_and_the_work(tmp_path, game, monkeypatch):
    s = make_session(tmp_path, game)
    s.emulator = SlowEmulator(game)
    cap = arrive(s)
    s.submit(cap, WELCOME)
    until(s, lambda: cap.state == REPLAYING)
    root = s.root

    def refuse(*a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "rename", refuse)
    assert not s.rehome(str(tmp_path / "p.mapchar.capture"))
    monkeypatch.undo()
    assert s.root == root and os.path.isdir(root)
    assert any("stay in" in m for m in s.messages)
    assert cap.state == WAITING and s.busy  # its turn comes again
    s.emulator = FakeEmulator(game)
    run_session(s)
    assert cap.state == DONE, cap.reason


def test_a_capture_left_busy_without_its_job_is_queued_again(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    cap.text, cap.state = WELCOME, TRACING
    assert s.job is None
    run_session(s)
    assert cap.state == DONE, cap.reason
