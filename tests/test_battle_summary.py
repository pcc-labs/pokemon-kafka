"""The start-of-battle snapshot and the end-of-battle rows, callable from outside the agent's loop.

The expedition rig drives fights through ``run_battle_turn`` and used to leave only per-turn rows;
these two methods are what give a supervisor-driven fight its ``battle_outcome``.
"""

from unittest.mock import MagicMock

from agent import SPECIES_ID_MAP
from memory_reader import BattleState
from test_agent import _make_agent


def _battle():
    return BattleState(
        battle_type=1,
        enemy_hp=20,
        enemy_max_hp=20,
        enemy_level=7,
        enemy_species=0x6B,
        enemy_type1=7,
        enemy_type2=7,
        player_hp=11,
        player_max_hp=61,
        player_level=21,
        player_species=5,
        moves=[10, 52, 0, 0],
    )


def test_snapshot_then_summary_emits_outcome_from_the_snapshot(tmp_path):
    ag = _make_agent(tmp_path)
    ag.collector = MagicMock()
    ag.memory.read_party_species = MagicMock(return_value=[5])
    ag.memory.read_party = MagicMock(return_value=[("Charmeleon", 21, 30)])
    ag.memory._read_party_hp = MagicMock(return_value=[30])
    ag.memory.find_healing_item = MagicMock(return_value=None)
    ag.turn_count = 40

    ag.snapshot_battle_start(_battle())
    assert ag._pre_battle_species == [5]
    assert ag._pre_battle_level == 21
    assert ag._battle_my_hp_start == 11 and ag._battle_my_max_hp == 61
    assert ag._battle_opponent_level == 7
    assert ag._battle_had_healing is False

    ag.turn_count = 47
    assert ag.emit_battle_summary(True, 7) == "won"
    ag.collector.battle_end.assert_called_once()
    ag.collector.encounter.assert_called_once()
    args = ag.collector.battle_outcome.call_args.args
    assert args[0] == 47
    assert args[1] == SPECIES_ID_MAP[5]  # the battler from the battle struct
    assert args[2] == 21 and args[3] == 11 and args[4] == 61 and args[5] == 30
    assert args[-2:] == (7, True)
    assert ag._pre_battle_species == [] and ag._pre_battle_level == 0


def test_outcome_names_the_battler_not_a_fainted_lead(tmp_path):
    ag = _make_agent(tmp_path)
    ag.collector = MagicMock()
    ag.memory.read_party_species = MagicMock(return_value=[97, 5])  # slot 1 fainted, slot 2 fights
    ag.memory.read_party = MagicMock(return_value=[("Hypno", 100, 0), ("Charmeleon", 21, 30)])
    ag.memory._read_party_hp = MagicMock(return_value=[0, 30])
    ag.memory.find_healing_item = MagicMock(return_value=None)
    ag.snapshot_battle_start(_battle())  # battle struct says player_species=5
    ag.emit_battle_summary(True, 2)
    assert ag.collector.battle_outcome.call_args.args[1] == SPECIES_ID_MAP[5]


def test_summary_reports_caught_and_lost(tmp_path):
    ag = _make_agent(tmp_path)
    ag.collector = MagicMock()
    ag.memory.read_party_species = MagicMock(return_value=[5])
    ag.memory._read_party_hp = MagicMock(return_value=[0])
    ag.memory.find_healing_item = MagicMock(return_value=None)

    ag.snapshot_battle_start(_battle())
    ag.memory.read_party = MagicMock(return_value=[("Charmeleon", 21, 30), ("Paras", 7, 20)])
    assert ag.emit_battle_summary(True, 3) == "caught"

    ag.snapshot_battle_start(_battle())
    ag.memory.read_party = MagicMock(return_value=[("Charmeleon", 21, 0)])
    assert ag.emit_battle_summary(False, 9) == "escaped_or_lost"
    assert ag.collector.battle_outcome.call_args.args[-1] is False
