import asyncio
import uuid

from .client import DecisionError


class Executor:
    def __init__(self, client):
        self.client = client
        self.owner = str(uuid.uuid4())
        self.tasks = {}

    def launch(self, db, run):
        key = (db.path, run["id"])
        task = asyncio.create_task(self.execute(db, run))
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def execute(self, db, run):
        if not db.start(run["id"], self.owner):
            return
        try:
            async with asyncio.timeout(55):
                raw, answers = await self.client.decide(run["request"])
            db.finish(run["id"], "succeeded", raw, answers, owner=self.owner)
        except asyncio.CancelledError:
            db.finish(
                run["id"], "interrupted", error="The server stopped this request before it completed.", owner=self.owner
            )
            raise
        except DecisionError as e:
            db.finish(run["id"], "failed", response=e.raw, error=str(e), owner=self.owner)
        except TimeoutError:
            db.finish(run["id"], "failed", error="The decision timed out. It has not been retried.", owner=self.owner)
        except Exception:
            db.finish(
                run["id"],
                "failed",
                error="The decision could not complete. Try again or check server configuration.",
                owner=self.owner,
            )

    def cancel(self, db, identity):
        db.finish(identity, "cancelled", error="Stopped by you. OpenRouter may already have processed this request.")
        task = self.tasks.get((db.path, identity))
        if task:
            task.cancel()
        return db.run(identity)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
