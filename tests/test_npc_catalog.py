"""The NPC catalog: sentences merged from the decoder's window reads, bodies joined to the sprite
table, fights and handouts attributed to the talk that caused them, coverage per map."""

import json

import npc_catalog as nc


def test_clean_said_merges_scrolling_reads():
    said = "Yo! Champ in making! | Even I don't | Even I don't know VIRIDIAN LEADER's | VIRIDIAN LEADER's identity!"
    assert nc.clean_said(said) == "Yo! Champ in making! Even I don't know VIRIDIAN LEADER's identity!"
    blocker = "A sleeping POKéMON bloc | A sleeping POKéMON block | A sleeping POKéMON blocks the way! | A sleeping"
    assert nc.clean_said(blocker) == "A sleeping POKéMON blocks the way!"
    assert nc.clean_said("Cell Separation System! | Cell Separation System!") == "Cell Separation System!"
    assert nc.clean_said("Hi. | Bye.") == "Hi. Bye."
    assert nc.clean_said(" | ") == ""


def _truth(tmp_path):
    truth = {
        "items": {"10": "MOON STONE", "232": "HM03"},
        "maps": {
            "26": {
                "sprites": [
                    {"kind": "trainer", "x": 53, "y": 10, "pic": 6},
                    {"kind": "npc", "x": 48, "y": 11, "pic": 18},
                    {"kind": "item", "x": 13, "y": 54, "pic": 61, "item": 10},
                ]
            },
            "88": {"sprites": [{"kind": "npc", "x": 6, "y": 3, "pic": 1}]},
        },
    }
    path = tmp_path / "rom_truth.json"
    path.write_text(json.dumps(truth))
    return path


def _sink(tmp_path, rows):
    d = tmp_path / "game"
    d.mkdir()
    (d / "2026-09-05.jsonl").write_text(
        "\n".join(json.dumps({"source": "expedition", **r}) for r in rows)
        + '\n{"not": "expedition"}\nnot json\n'
        + '{"source": "expedition", "event": "supervisor.body_engaged" broken\n'
        + '{"source": "expedition", "event": "screenshot", "run_id": "r1"}\n'
    )
    return d


def test_build_joins_sprites_fights_and_handouts(tmp_path):
    rows = [
        {"ts": "2026-09-05T16:46:40+00:00", "run_id": "r1", "event": "supervisor.leg_start", "pos": [26, 30, 12]},
        {"ts": "2026-09-05T16:46:44+00:00", "run_id": "r1", "event": "battle.outcome", "won": True},
        {
            "ts": "2026-09-05T16:46:45+00:00",
            "run_id": "r1",
            "event": "supervisor.body_engaged",
            "map": 26,
            "at": [53, 10],
            "said": "You look gentle, so I | You look gentle, so I thought",
            "gained": [],
        },
        {
            "ts": "2026-09-05T16:46:50+00:00",
            "run_id": "r1",
            "event": "supervisor.body_engaged",
            "map": 26,
            "at": [48, 11],
            "said": "Here, take this!",
            "gained": [[232, 1]],
        },
        {
            "ts": "2026-09-05T16:46:51+00:00",
            "run_id": "r1",
            "event": "supervisor.body_engaged",
            "map": 26,
            "at": [13, 54],
            "said": "found a MOON STONE!",
            "gained": [[10, 1]],
        },
        {"ts": "2026-09-05T16:46:52+00:00", "run_id": "r1", "event": "battle.fled", "pos": [26, 1, 1]},
        {
            "ts": "2026-09-05T16:47:30+00:00",
            "run_id": "r1",
            "event": "supervisor.body_engaged",
            "map": 26,
            "at": [1, 1],
            "said": "Got away safely!",
            "gained": [],
        },
        {
            "ts": "2026-09-05T16:48:00+00:00",
            "run_id": "r1",
            "event": "supervisor.blocker_engaged",
            "body": [10, 62],
            "said": "A sleeping POKéMON blocks the way!",
        },
        {
            "ts": "2026-09-05T16:48:00+00:00",
            "run_id": "r2",
            "event": "supervisor.blocker_engaged",
            "body": [1, 1],
            "said": "no map for this run",
        },
        {
            "ts": "2026-09-05T16:48:00+00:00",
            "run_id": "r2",
            "event": "supervisor.body_engaged",
            "map": 88,
            "said": "no cell",
            "gained": [],
        },
    ]
    rows += [
        {
            "ts": "2026-09-05T16:48:00+00:00",
            "run_id": "r3",
            "event": "supervisor.body_engaged",
            "map": 26,
            "at": [13, 54],
            "said": "OPTION EXIT",
            "gained": [],
        },
        {
            "ts": "2026-09-05T16:48:01+00:00",
            "run_id": "r3",
            "event": "supervisor.item_collected",
            "map": 26,
            "at": [13, 54],
            "items": [[10, 1]],
        },
        {
            "ts": "2026-09-05T16:48:02+00:00",
            "run_id": "r3",
            "event": "supervisor.item_refused",
            "map": 26,
            "at": [20, 20],
            "holds": "TM09",
        },
        {
            "ts": "2026-09-05T16:48:03+00:00",
            "run_id": "r3",
            "event": "supervisor.item_refused",
            "at": [20, 20],
            "holds": "no map",
        },
    ]
    sink = _sink(tmp_path, rows)
    cat = nc.build(sink, _truth(tmp_path))
    m26 = cat["maps"]["26"]
    trainer = m26["bodies"]["53,10"]
    assert trainer["kind"] == "trainer" and trainer["pic"] == 6
    assert trainer["fought"] == 1 and trainer["won"] == 1
    assert trainer["sentences"] == {"You look gentle, so I thought": 1}
    giver = m26["bodies"]["48,11"]
    assert giver["gained"] == {"HM03": 1} and giver["fought"] == 0
    ball = m26["bodies"]["13,54"]
    assert ball["kind"] == "item" and ball["item"] == "MOON STONE" and ball["gained"] == {"MOON STONE": 2}
    # a fled fight 38 s earlier is outside the window: not this body's
    assert m26["bodies"]["1,1"]["fought"] == 0 and m26["bodies"]["1,1"]["kind"] == "unknown"
    assert m26["bodies"]["10,62"]["blocker"] is True
    assert m26["sprites"] == 3 and m26["engaged"] == 6
    assert ball["runs"] == ["r1", "r3"]
    assert ball["noise"] == 1 and "OPTION EXIT" not in ball["sentences"]  # the START menu, not the ball
    refused = m26["bodies"]["20,20"]
    assert refused["refused"] == 1 and refused["kind"] == "unknown" and refused["seen"] == 0
    assert m26["items"] == [{"x": 13, "y": 54, "item": "MOON STONE", "picked": True}]
    # map 88 has a sprite but the only engage row had no cell; the blocker of r2 had no map
    assert cat["maps"]["88"]["engaged"] == 0 and cat["maps"]["88"]["sprites"] == 1
    assert trainer["runs"] == ["r1"] and trainer["seen"] == 1


def test_report_lists_unheard_maps_and_batons(tmp_path):
    sink = _sink(tmp_path, [])
    cat = nc.build(sink, _truth(tmp_path))
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"kg2_route14": {"map": 26}, "bill_pc": {"map": 88}, "broken": {"error": "x"}}))
    text = nc.report(cat, idx)
    lines = text.splitlines()
    assert lines[2].split()[:4] == ["26", "3", "0", "3"] and "kg2_route14" in lines[2]
    assert "bill_pc" in lines[3]
    assert "0 of 4 bodies talked to; 0 of 1 item balls picked; 0 maps with any talk" in lines[-1]
    assert "-" in nc.report(cat, tmp_path / "missing.json")


def test_main_build_then_report(tmp_path, capsys):
    sink = _sink(tmp_path, [])
    out = tmp_path / "cat.json"
    args = [
        "--telemetry",
        str(sink),
        "--truth",
        str(_truth(tmp_path)),
        "--out",
        str(out),
        "--baton-index",
        str(tmp_path / "none"),
    ]
    assert nc.main(["build", *args]) == 0
    assert json.loads(out.read_text())["maps"]["26"]["sprites"] == 3
    assert nc.main(["report", *args, "--limit", "1"]) == 0
    printed = capsys.readouterr().out
    assert "wrote" in printed and "bodies talked to" in printed


def test_ts_tolerates_garbage():
    assert nc._ts(None) is None and nc._ts("not a date") is None
