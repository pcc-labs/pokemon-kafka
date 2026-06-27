from __future__ import annotations

import asyncio

from viewer.live import LiveHub


def test_publish_fans_out_to_subscribers():
    async def scenario():
        hub = LiveHub()
        q1 = hub.subscribe("r")
        q2 = hub.subscribe("r")
        await hub.publish("r", {"n": 1})
        assert await asyncio.wait_for(q1.get(), 1) == {"n": 1}
        assert await asyncio.wait_for(q2.get(), 1) == {"n": 1}

    asyncio.run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario():
        hub = LiveHub()
        q = hub.subscribe("r")
        hub.unsubscribe("r", q)
        await hub.publish("r", {"n": 1})
        assert q.empty()

    asyncio.run(scenario())


def test_publish_unknown_run_is_noop():
    asyncio.run(LiveHub().publish("nobody", {"n": 1}))
