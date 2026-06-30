"""Coverage for the parcel-quest / WorldMap navigation branches added to agent.py.

These exercise the new overworld-decision branches (Mart cutscene, Viridian Forest piloting,
quest pilot/pilot_to overrides, ``_quest_target``), the run-loop hooks (wild-encounter marking,
trainer/Brock state capture + Brock recording, periodic WorldMap persistence), and the
``--worldmap-file`` wiring in ``main()``. The harness (``_make_agent``) is reused from test_agent.
"""

from unittest.mock import MagicMock, patch

import agent
from agent import main
from memory_reader import BattleState, OverworldState
from test_agent import _make_agent
from world_map import WorldMap


def _wild(**kw):
    d = dict(
        battle_type=1,
        player_hp=50,
        player_max_hp=100,
        enemy_hp=30,
        enemy_max_hp=40,
        moves=[0x01, 0x00, 0x00, 0x00],
        move_pp=[10, 0, 0, 0],
        player_level=5,
    )
    d.update(kw)
    return BattleState(**d)


# ---------------------------------------------------------------------------
# choose_overworld_action: parcel cutscene + forest + quest overrides
# ---------------------------------------------------------------------------


class TestOverworldQuestBranches:
    def test_mart_cutscene_presses_a(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag.memory.has_parcel = MagicMock(return_value=False)
        ag.memory.has_pokedex = MagicMock(return_value=False)
        state = OverworldState(map_id=42, x=4, y=5, party_count=1)
        assert ag.choose_overworld_action(state) == "a"  # line 763

    def test_text_box_emits_discovery_once_then_dedupes(self, tmp_path):
        # An active text box: decode the dialogue, emit a discovery event the first time, press A.
        # A second identical decode is deduped (no duplicate emit) but still presses A.
        ag = _make_agent(tmp_path)
        ag.memory.read_dialogue = MagicMock(return_value="TRAINER TIPS")
        ag.collector.discovery = MagicMock()
        state = OverworldState(map_id=51, x=3, y=4, party_count=1, text_box_active=True)

        assert ag.choose_overworld_action(state) == "a"
        assert ag._last_discovery == "TRAINER TIPS"
        ag.collector.discovery.assert_called_once_with(ag.turn_count, 51, 3, 4, "TRAINER TIPS")

        # Same text again -> deduped, no second emit.
        assert ag.choose_overworld_action(state) == "a"
        ag.collector.discovery.assert_called_once()

    def test_forest_pilots_toward_exit_when_known_reachable(self, tmp_path):
        # When the exit is reachable over KNOWN-walkable tiles, commit to it via plan_step.
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag.world.known_reachable = MagicMock(return_value=True)
        ag.world.plan_step = MagicMock(return_value="up")
        state = OverworldState(map_id=51, x=10, y=20, party_count=1)
        assert ag.choose_overworld_action(state) == "up"
        ag.world.plan_step.assert_called_once()

    def test_forest_uses_frontier_step_when_not_known_reachable(self, tmp_path):
        # When the exit isn't yet known-reachable (and we're not stuck), systematically map the
        # maze with the robust frontier explorer rather than beelining at unknown walls.
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag.stuck_turns = 0
        ag.world.known_reachable = MagicMock(return_value=False)
        ag.world.frontier_step = MagicMock(return_value="left")
        state = OverworldState(map_id=51, x=10, y=20, party_count=1)
        assert ag.choose_overworld_action(state) == "left"
        ag.world.frontier_step.assert_called_once()

    def test_forest_falls_back_to_waypoint_pilot_when_frontier_exhausted(self, tmp_path):
        # Only once the reachable area is fully mapped (frontier_step None) does the agent fall back
        # to optimistically piloting the waypoint chain toward the exit.
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag.stuck_turns = 0
        ag.world.known_reachable = MagicMock(return_value=False)
        ag.world.frontier_step = MagicMock(return_value=None)
        ag._pilot_to = MagicMock(return_value="left")
        state = OverworldState(map_id=51, x=10, y=20, party_count=1)
        assert ag.choose_overworld_action(state) == "left"
        ag._pilot_to.assert_called()

    def test_forest_falls_back_to_cross_step_at_exit(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag.world.known_reachable = MagicMock(return_value=True)
        ag.world.cross_step = MagicMock(return_value="down")
        # Default exit (2, 0); standing on it makes _pilot_to return None -> cross_step sweep.
        state = OverworldState(map_id=51, x=2, y=0, party_count=1)
        assert ag.choose_overworld_action(state) == "down"
        ag.world.cross_step.assert_called_once()

    def test_quest_pilot_uses_cross_step(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag._quest_target = MagicMock(return_value={"pilot": "north"})
        ag.world.cross_step = MagicMock(return_value="up")
        state = OverworldState(map_id=12, x=5, y=5, party_count=1)
        assert ag.choose_overworld_action(state) == "up"  # 871-873 + 899
        assert ag.navigator.quest_target is None

    def test_quest_pilot_to_moves_then_interacts(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 0
        ag._quest_target = MagicMock(return_value={"pilot_to": (5, 5), "at_target": "b"})
        ag.world.plan_step = MagicMock(return_value="right")
        # Not arrived -> returns the pilot direction (874-879).
        moving = OverworldState(map_id=12, x=3, y=3, party_count=1)
        assert ag.choose_overworld_action(moving) == "right"
        # Arrived -> _pilot_to None -> toggle alternates facing-press / "a" (880-883).
        arrived = OverworldState(map_id=12, x=5, y=5, party_count=1)
        assert ag.choose_overworld_action(arrived) == "b"  # toggle True
        assert ag.choose_overworld_action(arrived) == "a"  # toggle False

    def test_quest_target_none_without_party(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag._quest_target(OverworldState(map_id=12, party_count=0)) is None  # 916-917

    def test_quest_target_builds_and_logs_on_new_map(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.memory.has_parcel = MagicMock(return_value=False)
        ag.memory.has_pokedex = MagicMock(return_value=False)
        ag.memory.read_player_facing_name = MagicMock(return_value="down")
        ag.parcel_quest.next_target = MagicMock(return_value={"name": "mart"})
        ag.parcel_quest.describe = MagicMock(return_value="TO_MART")
        ag._last_logged_map = -1  # force the one-time map-transition log branch
        target = ag._quest_target(OverworldState(map_id=12, x=5, y=5, party_count=1))
        assert target == {"name": "mart"}  # 918-933
        assert ag._last_logged_map == 12
        assert any("QUEST" in e for e in ag.events)


# ---------------------------------------------------------------------------
# run() loop hooks: wild-encounter marking, trainer/Brock capture + recording
# ---------------------------------------------------------------------------


class TestRunLoopHooks:
    def _battle_helpers(self, ag):
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])
        ag.memory.player_whited_out = MagicMock(return_value=False)
        ag.memory.read_party = MagicMock(return_value=[])

    def test_wild_battle_marks_encounter_tile(self, tmp_path):
        ag = _make_agent(tmp_path)
        self._battle_helpers(ag)
        ag.world.mark_encounter = MagicMock()
        ag.last_overworld_state = OverworldState(map_id=12, x=7, y=8)
        ag.memory.read_battle_state = MagicMock(
            side_effect=[_wild(battle_type=1), _wild(battle_type=1), BattleState(battle_type=0)]
        )
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=12, x=7, y=8))
        with patch.object(agent, "Image", None):
            ag.run(max_turns=1)
        ag.world.mark_encounter.assert_called_once_with(12, 7, 8)  # 1440-1441

    def test_trainer_save_and_brock_record_by_level(self, tmp_path):
        ag = _make_agent(tmp_path)
        self._battle_helpers(ag)
        path = tmp_path / "pre_brock.state"
        ag.memory.read_battle_state = MagicMock(
            side_effect=[
                _wild(battle_type=2, enemy_level=12),
                _wild(battle_type=2, enemy_level=12),
                BattleState(battle_type=0),
            ]
        )
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=2, x=5, y=5))
        with patch.object(agent, "Image", None):
            ag.run(max_turns=1, save_state_on_trainer=f"brock:{path}")
        assert ag._trainer_state_saved is True  # 1403-1405 + 1457-1460
        assert ag.brock_turns is not None  # 1517-1522
        assert ag.brock_won is False  # Boulder badge bit unset in (zeroed) memory

    def test_trainer_target_by_map_id_parse(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.run_overworld = MagicMock()
        ag.memory.read_battle_state = MagicMock(return_value=BattleState(battle_type=0))
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        with patch.object(agent, "Image", None):
            ag.run(max_turns=1, save_state_on_trainer=f"54:{tmp_path / 't.state'}")
        assert ag._trainer_state_saved is False  # 1406-1407 (parse only; no matching battle)

    def test_periodic_and_final_worldmap_save(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.worldmap_file = str(tmp_path / "wm.json")
        ag.world = MagicMock()

        def bump():
            ag.turn_count += 1

        ag.run_overworld = MagicMock(side_effect=bump)
        ag.memory.read_battle_state = MagicMock(return_value=BattleState(battle_type=0))
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        with patch.object(agent, "Image", None):
            ag.run(max_turns=500)
        # Periodic save at turn 500 (1570-1571) plus the final persist (1573-1574).
        assert ag.world.save.call_count >= 2


# ---------------------------------------------------------------------------
# main() --worldmap-file wiring + WorldMap.load corrupt-file fallback
# ---------------------------------------------------------------------------


class TestMainWorldmapAndLoad:
    def test_main_resumes_worldmap(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("rom")
        wm = tmp_path / "wm.json"
        mock_agent = MagicMock()
        with (
            patch("sys.argv", ["agent.py", str(rom), "--max-turns", "1", "--worldmap-file", str(wm)]),
            patch("agent.PokemonAgent", return_value=mock_agent),
        ):
            main()
        assert mock_agent.worldmap_file == str(wm)  # 1681-1683

    def test_worldmap_load_corrupt_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        wm = WorldMap.load(str(p))  # world_map.py 108-109
        assert isinstance(wm, WorldMap)
