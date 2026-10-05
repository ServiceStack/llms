"""Opt-in HTTP/public viewer checks against an isolated development publisher."""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publisher", default="http://127.0.0.1:5127")
    parser.add_argument("--reference", default="fixture")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--screenshot")
    args = parser.parse_args()
    base = args.publisher.rstrip("/")
    reference = args.reference
    with urllib.request.urlopen(base + "/publish/decision/" + reference) as response:
        detail = json.load(response)
        assert response.headers["Cache-Control"] == "private, no-cache, must-revalidate"
        assert detail["execution"]["status"] == "succeeded"
        assert "publishedBy" not in detail and "createIdempotencyKey" not in detail
        assert response.headers["ETag"]
    with urllib.request.urlopen(base + "/publish/decisions/tags") as response:
        tags = json.load(response)
        assert tags["version"] == 3 and len(tags["tags"]) == 12
        assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert detail["downloadUrl"] == base + "/d/" + reference + ".json"
    for suffix in (".json", "/recipe.json"):
        with urllib.request.urlopen(base + "/d/" + reference + suffix) as response:
            assert json.load(response) == detail["document"]
            assert response.headers.get_content_type() == "application/json"
            assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    with urllib.request.urlopen(base + "/publish/decisions?take=500&skip=-1") as response:
        catalog = json.load(response)
        assert catalog["take"] == 50 and catalog["skip"] == 0
        assert any(row["externalRef"] == reference for row in catalog["items"])
        assert all(row["document"] is None and row["execution"] is None for row in catalog["items"])
    for path in ("/publish/decision/missing", "/publish/decisions?orderBy=arbitrary-column"):
        try:
            urllib.request.urlopen(base + path)
            raise AssertionError("Expected unavailable/invalid request: " + path)
        except urllib.error.HTTPError as error:
            assert error.code in (400, 404)
    # Mutations only target loopback fixture hosts, never a production catalog.
    assert urllib.parse.urlsplit(base).hostname in ("127.0.0.1", "localhost", "::1")
    fixture = next(
        case
        for case in json.loads((Path(__file__).parent / "fixtures/jev-sharing-contract.json").read_text())
        if case["name"] == "recorded-usage-example"
    )
    fixture["document"]["content"] = "Email"
    fixture["document"]["tags"] = ["Sentiment", "Urgency", "custom-tag"]
    payload = {key: fixture[key] for key in ("filename", "document", "execution")}
    payload["idempotencyKey"] = uuid.uuid4().hex

    def mutate(method, path, body=None, key="recipe-fixture-key-alice", status=200):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                assert response.status == status
                return json.load(response)
        except urllib.error.HTTPError as error:
            assert error.code == status, (error.code, error.read().decode())

    created = mutate("POST", "/publish/decision", payload)
    retried = mutate("POST", "/publish/decision", payload)
    assert created["externalRef"] == retried["externalRef"]
    assert created["content"] == "Email" and created["tags"] == fixture["document"]["tags"]
    ref = created["externalRef"]
    with urllib.request.urlopen(base + "/publish/decision/" + ref) as response:
        assert (
            json.load(response)["document"]["examples"][0]["execution"]
            == fixture["document"]["examples"][0]["execution"]
        )
    update = {**payload, "revision": created["revision"]}
    update["document"] = {**payload["document"], "description": "A reviewed update"}
    mutate("PUT", "/publish/decision/" + ref, update, key="recipe-fixture-key-bob", status=403)
    changed = mutate("PUT", "/publish/decision/" + ref, update)
    assert changed["revision"] == 2 and changed["publishedUrl"] == created["publishedUrl"]
    mutate("DELETE", "/publish/decision/" + ref + "?revision=1", status=409)
    mutate("DELETE", "/publish/decision/" + ref + "?revision=2")
    mutate("DELETE", "/publish/decision/" + ref + "?revision=2")
    with urllib.request.urlopen(
        urllib.request.Request(
            base + "/publish/decisions/mine",
            headers={"Accept": "application/json", "Authorization": "Bearer recipe-fixture-key-alice"},
        )
    ) as response:
        assert all(item["externalRef"] != ref for item in json.load(response)["items"])

    # Exercise the actual Python orchestration/client against the C# publisher.
    async def local_round_trip():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from llms.extensions.jev.schema import compile_request
        from llms.extensions.jev.sharing import Sharing
        from llms.extensions.jev.storage import ConflictError, JevStore, write_json

        with tempfile.TemporaryDirectory(prefix="jev-sharing-http-") as root:
            store = JevStore(root)
            ctx = SimpleNamespace(app=SimpleNamespace(publisher_available=True), get_user_path=lambda user=None: root)
            write_json(
                Path(root) / "publish/config.json",
                {"baseUrl": base, "allowHttp": True, "apiKey": "recipe-fixture-key-alice", "userId": "fixture-owner"},
            )
            sharing = Sharing(ctx)
            row = store.save(fixture["document"], filename=fixture["filename"])

            def executed(model, key):
                run, _ = store.submit(
                    key,
                    row["document"],
                    fixture["execution"]["input"],
                    compile_request(row["document"], fixture["execution"]["input"]),
                    "fixture-lease",
                    row["id"],
                    row["revision"],
                )
                store.finish(run["id"], "succeeded", {"model": model}, fixture["execution"]["answers"])
                return run["id"]

            run = executed(fixture["execution"]["model"], "fixture-run-1")
            receipt = await sharing.publish("fixture", store, row["id"], {"revision": row["revision"], "runId": run})
            assert "publisherRunCount" not in receipt and not receipt["publisherStarred"]
            store.favourite(row["id"], True)
            preview = await sharing.preview("fixture", receipt["publishedUrl"])
            assert preview["execution"]["answers"] == fixture["execution"]["answers"]
            assert (await sharing.status("fixture", store, row["id"]))["publication"]["publicRevision"] == 1
            run2 = executed("fixture-alternate-model", "fixture-run-2")
            updated = await sharing.publish(
                "fixture", store, row["id"], {"revision": row["revision"], "runId": run2, "publishedRevision": 1}
            )
            assert "publisherRunCount" not in updated and updated["publisherStarred"]
            assert updated["revision"] == 2 and updated["recipeHash"] == receipt["recipeHash"]
            pinned = {
                "externalRef": receipt["externalRef"],
                "publishedRevision": 1,
                "contentHash": receipt["contentHash"],
                "filename": "community.copy.json",
            }
            try:
                await sharing.import_recipe("fixture", store, pinned)
                raise AssertionError("Stale source must not import")
            except ConflictError:
                pass
            imported = await sharing.import_recipe(
                "fixture", store, {**pinned, "publishedRevision": 2, "contentHash": updated["contentHash"]}
            )
            assert imported["sourceExample"]["model"] == "fixture-alternate-model"
            assert "publication" not in imported
            assert (
                imported["document"]["content"] == "Email"
                and imported["document"]["tags"] == fixture["document"]["tags"]
            )
            assert imported["document"]["examples"][0]["execution"] == fixture["document"]["examples"][0]["execution"]
            assert not (await sharing.status("fixture", store, imported["id"]))["runs"]
            await sharing.unpublish("fixture", store, row["id"], {"publishedRevision": 2})
            assert store.recipe(imported["id"])["sourceExample"]
            assert store.run(run) and store.run(run2)

    asyncio.run(local_round_trip())
    chromium = shutil.which("chromium") or shutil.which("google-chrome")
    with tempfile.TemporaryDirectory(prefix="jev-public-browser-") as profile:
        command = [
            chromium,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--user-data-dir=" + profile,
            "--virtual-time-budget=10000",
            "--window-size=" + str(args.width) + ",1100",
            "--dump-dom",
        ]
        if args.screenshot:
            command.append("--screenshot=" + args.screenshot)
        command.append(base + "/d/" + reference + ("?dark" if args.dark else "?light"))
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        html = result.stdout
        assert "Recorded example result" in html, result.stderr[-1200:]
        assert 'class="jev-answer"' in html, html[-1000:]
        assert "Recorded prompt / state" in html
        assert "Measured accuracy" not in html
        assert "Run decision" not in html
    print(
        "PASS: public APIs, Python/C# share-update-import round trip, portable download, bounded catalog and immediate recorded viewer results"
    )


if __name__ == "__main__":
    main()
