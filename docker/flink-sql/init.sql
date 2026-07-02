-- Session recording moved to Paper (paperd). The agent.telemetry.raw stream
-- and its LLM-turn detectors (STUCK_LOOP, TOKEN_SPIKE) were retired with the
-- Tapes proxy. Anomaly detection now runs entirely off agent.game.events below.

-- Sink table: writes alerts to Kafka
CREATE TABLE tapes_alerts (
    `alert_type` STRING,
    `root_hash` STRING,
    `detail` STRING,
    `window_start` TIMESTAMP(3),
    `window_end` TIMESTAMP(3),
    `event_count` BIGINT
) WITH (
    'connector' = 'kafka',
    'topic' = 'agent.telemetry.alerts',
    'properties.bootstrap.servers' = 'kafka:29092',
    'format' = 'json'
);

-- ============================================================
-- Game Events: reads pokemon.game.v1 events from Kafka
-- ============================================================
-- Union schema: `data` is a flat ROW containing fields from ALL event types
-- (battle, overworld, map_change, stuck, milestone, session). Most fields
-- are NULL for any given event. This avoids per-type tables while keeping
-- queries simple — filter on `event_type` to get the relevant columns.
CREATE TABLE game_events (
    `schema` STRING,
    `event_type` STRING,
    `turn` INT,
    `occurred_at` TIMESTAMP_LTZ(3),
    `data` ROW<
        `map_id` INT,
        `position` ROW<`x` INT, `y` INT>,
        `player_hp` INT,
        `player_max_hp` INT,
        `enemy_hp` INT,
        `enemy_max_hp` INT,
        `action` STRING,
        `prev_map` INT,
        `new_map` INT,
        `badges` INT,
        `party_count` INT,
        `stuck_turns` INT,
        `streak` INT,
        `last_action` STRING,
        `description` STRING,
        `phase` STRING,
        `battles_won` INT,
        `maps_visited` INT
    >,
    WATERMARK FOR `occurred_at` AS `occurred_at` - INTERVAL '5' SECONDS
) WITH (
    'connector' = 'kafka',
    'topic' = 'agent.game.events',
    'properties.bootstrap.servers' = 'kafka:29092',
    'properties.group.id' = 'flink-game',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.timestamp-format.standard' = 'ISO-8601',
    'json.ignore-parse-errors' = 'true'
);

-- Game alerts sink (reuses existing tapes_alerts table)

-- Navigation stuck detection: 5+ stuck events in a 60s window
INSERT INTO tapes_alerts
SELECT
    'GAME_STUCK_LOOP' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING), ' streak=', CAST(MAX(data.streak) AS STRING)) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(
        TABLE game_events,
        DESCRIPTOR(occurred_at),
        INTERVAL '60' SECONDS
    )
)
WHERE event_type = 'stuck'
GROUP BY data.map_id, window_start, window_end
HAVING COUNT(*) >= 5;

-- Battle loss detection: battles where player HP hits 0 in a 5-minute window
INSERT INTO tapes_alerts
SELECT
    'BATTLE_WIPE' AS alert_type,
    '' AS root_hash,
    CONCAT('wipes=', CAST(COUNT(*) AS STRING)) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(
        TABLE game_events,
        DESCRIPTOR(occurred_at),
        INTERVAL '5' MINUTES
    )
)
WHERE event_type = 'battle' AND data.player_hp = 0
GROUP BY window_start, window_end
HAVING COUNT(*) >= 1;

-- Battle loop detection: 20+ battle events with same enemy_hp in 30s
-- Catches input spam where the agent fights without dealing damage
-- (e.g., frame waits too short, moves not registering)
INSERT INTO tapes_alerts
SELECT
    'BATTLE_LOOP' AS alert_type,
    '' AS root_hash,
    CONCAT('enemy_hp=', CAST(data.enemy_hp AS STRING),
           ' player_hp=', CAST(MIN(data.player_hp) AS STRING)) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '30' SECONDS)
)
WHERE event_type = 'battle'
GROUP BY data.enemy_hp, window_start, window_end
HAVING COUNT(*) >= 20;

-- Position deadlock: 50+ overworld events at same position in 2 minutes
-- Catches the agent bouncing against an impassable obstacle (ledge, tree)
INSERT INTO tapes_alerts
SELECT
    'POSITION_DEADLOCK' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING),
           ' pos=(', CAST(data.position.x AS STRING), ',',
           CAST(data.position.y AS STRING), ')') AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '2' MINUTES)
)
WHERE event_type = 'overworld'
GROUP BY data.map_id, data.position.x, data.position.y, window_start, window_end
HAVING COUNT(*) >= 50;

-- No progress: 100+ overworld events on same map hitting <=5 unique positions in 5 min
-- Higher-level signal that navigation is completely stalled
INSERT INTO tapes_alerts
SELECT
    'NO_PROGRESS' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING),
           ' turns=', CAST(COUNT(*) AS STRING)) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '5' MINUTES)
)
WHERE event_type = 'overworld'
GROUP BY data.map_id, window_start, window_end
HAVING COUNT(*) >= 100
   AND COUNT(DISTINCT CONCAT(CAST(data.position.x AS STRING), ',', CAST(data.position.y AS STRING))) <= 5;

-- ============================================================
-- Stuck-detection rules (value-based, added 2026-07)
-- ============================================================
-- The rules above are COUNT-based over a window. But the agent throttles event
-- emission while wedged (roughly one overworld event per 50 stuck turns), so a
-- deep wedge emits only a handful of events and slips under the count thresholds
-- of POSITION_DEADLOCK/NO_PROGRESS. These three rules key on the severity VALUES
-- the events already carry (`stuck_turns`, `streak`) so a single hard wedge
-- fires without needing 50-100 events. Thresholds derived from real sessions:
-- Oak's Lab (map 40) A-mash/direction wedges hit streak ~20; an interior wedge
-- (map 37) reached stuck_turns=531.

-- In-place wedge: a single tile where stuck_turns climbs high. Value-based
-- complement to POSITION_DEADLOCK — catches deep wedges that emit too few
-- (throttled) events to trip the 50-event count threshold.
INSERT INTO tapes_alerts
SELECT
    'IN_PLACE_WEDGE' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING),
           ' pos=(', CAST(data.position.x AS STRING), ',',
           CAST(data.position.y AS STRING), ')',
           ' stuck_turns=', CAST(MAX(data.stuck_turns) AS STRING)) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '2' MINUTES)
)
WHERE event_type = 'overworld' AND data.stuck_turns >= 100
GROUP BY data.map_id, data.position.x, data.position.y, window_start, window_end;

-- Stuck streak spike: one hard thrash where the failed-move streak climbs fast.
-- GAME_STUCK_LOOP needs 5+ stuck events per map in the window; this fires on the
-- streak VALUE, so a single deep thrash (streak >= 15) alerts even from a couple
-- of events. MAX(last_action) surfaces what the agent was mashing.
INSERT INTO tapes_alerts
SELECT
    'STUCK_STREAK_SPIKE' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING),
           ' streak=', CAST(MAX(data.streak) AS STRING),
           ' last_action=', COALESCE(MAX(data.last_action), '?')) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '60' SECONDS)
)
WHERE event_type = 'stuck' AND data.streak >= 15
GROUP BY data.map_id, window_start, window_end;

-- Door / threshold stall: the agent re-stalls on the SAME tile with the SAME
-- action several times in a window — the door-cooldown / A-mash-at-NPC signature
-- seen at building entrances (Oak's Lab map 40, Viridian Mart map 42). More
-- precise than GAME_STUCK_LOOP (which groups by map only): grouping by tile and
-- action catches short, recurrent stalls at one doorway with a lower threshold.
INSERT INTO tapes_alerts
SELECT
    'DOOR_STALL' AS alert_type,
    '' AS root_hash,
    CONCAT('map=', CAST(data.map_id AS STRING),
           ' pos=(', CAST(data.position.x AS STRING), ',',
           CAST(data.position.y AS STRING), ')',
           ' action=', COALESCE(data.last_action, '?')) AS detail,
    window_start,
    window_end,
    COUNT(*) AS event_count
FROM TABLE(
    TUMBLE(TABLE game_events, DESCRIPTOR(occurred_at), INTERVAL '90' SECONDS)
)
WHERE event_type = 'stuck'
GROUP BY data.map_id, data.position.x, data.position.y, data.last_action, window_start, window_end
HAVING COUNT(*) >= 3;
