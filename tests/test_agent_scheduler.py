import asyncio
import unittest

from llms.extensions.app import AgentScheduler


class FakeRunDb:
    def __init__(self, count, interrupted=False):
        status = "running" if interrupted else "queued"
        self.runs = {
            i: {"id": i, "threadId": i, "user": "test", "status": status}
            for i in range(1, count + 1)
        }
        self.claim_calls = 0
        self.thread_updates = []

    def requeue_interrupted_agent_runs(self):
        recovered = 0
        for run in self.runs.values():
            if run["status"] == "running":
                run["status"] = "queued"
                recovered += 1
        return recovered

    def claim_agent_runs(self, owner, limit, lease_seconds):
        self.claim_calls += 1
        claimed = []
        for run in self.runs.values():
            if run["status"] == "queued" and len(claimed) < limit:
                run.update(status="running", leaseOwner=owner)
                claimed.append(dict(run))
        return claimed

    def renew_agent_run_lease(self, run_id, owner, lease_seconds):
        run = self.runs[run_id]
        return int(run["status"] == "running" and run.get("leaseOwner") == owner)

    def get_agent_run(self, run_id, user=None):
        return self.runs.get(run_id)

    def update_agent_run(self, run_id, values):
        self.runs[run_id].update(values)
        return 1

    async def update_thread_async(self, thread_id, values, user=None):
        self.thread_updates.append((thread_id, values, user))
        return 1


class AgentSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_bounds_concurrency_and_drains_durable_queue(self):
        db = FakeRunDb(5)
        active = 0
        maximum = 0

        async def execute(run):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            db.update_agent_run(run["id"], {"status": "completed"})
            active -= 1

        scheduler = AgentScheduler(db, execute, lambda *_: None, max_concurrency=2, poll_seconds=0.1)
        scheduler.start()
        try:
            async with asyncio.timeout(2):
                while any(run["status"] != "completed" for run in db.runs.values()):
                    await asyncio.sleep(0.02)
        finally:
            await scheduler.stop()

        self.assertEqual(maximum, 2)
        self.assertTrue(all(run["status"] == "completed" for run in db.runs.values()))

    async def test_start_recovers_interrupted_runs(self):
        db = FakeRunDb(1, interrupted=True)

        async def execute(run):
            db.update_agent_run(run["id"], {"status": "completed"})

        scheduler = AgentScheduler(db, execute, lambda *_: None, max_concurrency=1, poll_seconds=0.1)
        scheduler.start()
        try:
            async with asyncio.timeout(1):
                while db.runs[1]["status"] != "completed":
                    await asyncio.sleep(0.01)
        finally:
            await scheduler.stop()

        self.assertEqual(db.runs[1]["status"], "completed")

    async def test_idle_scheduler_does_not_poll_the_database(self):
        db = FakeRunDb(0)
        scheduler = AgentScheduler(db, lambda run: None, lambda *_: None, poll_seconds=0.1)
        scheduler.start()
        try:
            await asyncio.sleep(0.25)
            self.assertEqual(db.claim_calls, 1)
            scheduler.wake()
            await asyncio.sleep(0.05)
            self.assertEqual(db.claim_calls, 2)
        finally:
            await scheduler.stop()

    async def test_stop_requeues_an_in_flight_run(self):
        db = FakeRunDb(1)
        started = asyncio.Event()

        async def execute(run):
            started.set()
            await asyncio.Event().wait()

        scheduler = AgentScheduler(db, execute, lambda *_: None, max_concurrency=1, poll_seconds=0.1)
        scheduler.start()
        await asyncio.wait_for(started.wait(), timeout=1)
        await scheduler.stop()

        self.assertEqual(db.runs[1]["status"], "queued")
        self.assertIsNone(db.runs[1].get("leaseOwner"))

    async def test_unhandled_slice_error_fails_run_and_thread(self):
        db = FakeRunDb(1)

        async def execute(_):
            raise ValueError("context exceeds provider limit")

        scheduler = AgentScheduler(
            db, execute, lambda *_: None, lambda ex: str(ex),
            max_concurrency=1, poll_seconds=0.1,
        )
        scheduler.start()
        try:
            async with asyncio.timeout(1):
                while db.runs[1]["status"] != "failed":
                    await asyncio.sleep(0.01)
        finally:
            await scheduler.stop()

        self.assertEqual(db.runs[1]["error"], "context exceeds provider limit")
        self.assertIsNone(db.runs[1].get("leaseOwner"))
        self.assertEqual(db.thread_updates[0][0], 1)
        self.assertEqual(db.thread_updates[0][1]["error"], "context exceeds provider limit")
