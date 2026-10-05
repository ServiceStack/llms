"""Publish immutable, executed snapshots; import independent source-pinned copies."""

import copy
import time
import uuid
from datetime import UTC, datetime

from aiohttp import web

from llms.extensions.publish.client import PublisherClient, get_publish_config, public_reference, reference_from_url

from .recorded import bounded_json, validate_execution
from .importing import download_recipe, import_url
from .schema import compile_request, require, text, validate_recipe
from .storage import ConflictError, document_hash, read_json, recipe_filename, write_json


def execution_package(document, run):
    require(run.get("status") == "succeeded", "runId", "Select a successful local execution.")
    require(
        document_hash({k: v for k, v in (run.get("recipe") or {}).items() if k != "examples"})
        == document_hash({k: v for k, v in document.items() if k != "examples"}),
        "runId",
        "Saved changes require a matching successful execution.",
    )
    compiled = compile_request(document, run["input"])
    require(run.get("request") == compiled, "runId", "The execution request does not match this recipe.")
    response = run.get("response") if isinstance(run.get("response"), dict) else {}
    package = {
        "status": "succeeded",
        "input": run["input"],
        "prompt": run["request"]["state"],
        "answers": run.get("answers"),
        "model": response.get("model") or compiled["model"],
        "completedAt": datetime.fromtimestamp(run["completedAt"], UTC).isoformat(),
    }
    if run.get("durationMs") is not None:
        package["durationMs"] = run["durationMs"]
    return validate_execution(document, package)


def eligible_runs(db, identity, document):
    result = []
    for _, run in db._runs():
        if run.get("recipeId") != identity:
            continue
        try:
            execution = execution_package(document, run)
            result.append({"id": run["id"], "execution": execution})
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return sorted(result, key=lambda r: (r["execution"]["completedAt"], r["id"]), reverse=True)


def validate_detail(detail):
    bounded_json(detail)
    require(isinstance(detail, dict), "source", "Expected a publication.")
    recipe_filename(detail.get("filename"))
    public_reference(detail.get("externalRef"))
    require(type(detail.get("revision")) is int and detail["revision"] > 0, "revision", "Invalid public revision.")
    text(detail.get("contentHash"), "contentHash", 64)
    detail["document"] = validate_recipe(detail.get("document"))
    detail["execution"] = validate_execution(detail["document"], detail.get("execution"))
    return detail


class Sharing:
    def __init__(self, ctx):
        self.ctx = ctx

    def client(self, user):
        if not getattr(self.ctx.app, "publisher_available", False):
            raise web.HTTPServiceUnavailable(text="Enable the publish extension to share and browse recipes.")
        return PublisherClient(get_publish_config(self.ctx, user, obscure=False))

    @staticmethod
    def bound(binding, client):
        return (
            binding
            and binding.get("baseUrl") == client.base
            and binding.get("publisherAccount") == client.config.get("userId")
        )

    async def status(self, user, db, identity):
        client = self.client(user)
        with db.transaction() as index:
            row = next((r for r in db._recipes(index) if r["id"] == identity), None)
            if not row:
                raise web.HTTPNotFound(text="Recipe not found.")
            binding = copy.deepcopy(index["recipes"][identity].get("publication"))
            runs = eligible_runs(db, identity, row["document"])
            usage = self.usage(db, index, identity)
            pending = copy.deepcopy(index["recipes"][identity].get("pendingPublication"))
            captured = None
            if pending:
                captured = {
                    "reservation": {
                        key: pending[key] for key in ("savedRevision", "sourceRunId", "includeAdditionalExamples")
                    },
                    "payload": read_json(db.root / pending["pendingPayloadRef"]),
                }
        if not self.bound(binding, client):
            binding = None
        if binding:
            try:
                remote = await client.request("GET", "/publish/decision/" + public_reference(binding["externalRef"]))
                changed = binding.get("remoteChanged", False) or binding["contentHash"] != remote["contentHash"]
                binding.update(
                    publicRevision=remote["revision"], contentHash=remote["contentHash"], remoteChanged=changed
                )
                with db.transaction() as index:
                    meta = index["recipes"].get(identity, {})
                    if meta.get("publication", {}).get("externalRef") == binding["externalRef"]:
                        meta["publication"].update(
                            publicRevision=remote["revision"], contentHash=remote["contentHash"], remoteChanged=changed
                        )
            except web.HTTPNotFound:
                # An owner-validated conditional delete reconciles a revoked share's receipt.
                await client.request(
                    "DELETE",
                    "/publish/decision/"
                    + public_reference(binding["externalRef"])
                    + "?revision="
                    + str(binding["publicRevision"]),
                    authenticated=True,
                )
                with db.transaction() as index:
                    meta = index["recipes"].get(identity, {})
                    if meta.get("publication", {}).get("externalRef") == binding["externalRef"]:
                        meta.pop("publication", None)
                binding = None
        return {
            "account": get_publish_config(self.ctx, user),
            "recipe": row,
            "publication": binding,
            "pendingPublication": captured,
            "runs": runs,
            "usage": usage,
            "message": "" if runs else "Run this recipe first",
            "savedChangesNotShared": bool(
                binding
                and (
                    binding.get("remoteChanged")
                    or (binding.get("includeAdditionalExamples") is False and bool(row["document"].get("examples")))
                    or binding.get("savedDocumentHash") != document_hash(row["document"])
                    or any(binding.get(key) != value for key, value in usage.items())
                )
            ),
        }

    async def publish(self, user, db, identity, body):
        client = self.client(user)
        with db.transaction() as index:
            row = next((r for r in db._recipes(index) if r["id"] == identity), None)
            require(row is not None, "recipe", "Save this recipe first.")
            metadata = index["recipes"][identity]
            pending = metadata.get("pendingPublication")
            if pending:
                require(
                    self.bound(pending, client),
                    "account",
                    "Reconnect the account with the pending publication to recover its outcome.",
                )
                payload = read_json(db.root / "pending" / (pending["idempotencyKey"] + ".json"))
                require(payload is not None, "publication", "Pending publication journal is missing.")
            else:
                require(
                    row["revision"] == body.get("revision"), "revision", "Saved recipe changed. Refresh before sharing."
                )
                document = validate_recipe(row["document"])
                run = next(
                    (r for _, r in db._runs() if r["id"] == body.get("runId") and r.get("recipeId") == identity), None
                )
                require(run is not None, "runId", "Select an execution from this recipe's local history.")
                execution = execution_package(document, run)
                payload = {
                    "filename": row["filename"],
                    "document": copy.deepcopy(document),
                    "execution": execution,
                    **self.usage(db, index, identity),
                }
                binding = metadata.get("publication")
                pending = {
                    "baseUrl": client.base,
                    "publisherAccount": client.config.get("userId"),
                    "idempotencyKey": uuid.uuid4().hex,
                    "savedRevision": row["revision"],
                    "savedDocumentHash": document_hash(document),
                    "sourceRunId": run["id"],
                    # Keep this receipt marker to identify older shares that omitted documentation.
                    "includeAdditionalExamples": True,
                    "payloadHash": document_hash(payload),
                }
                if self.bound(binding, client):
                    require(
                        body.get("publishedRevision") == binding["publicRevision"],
                        "publishedRevision",
                        "Review the latest public revision before updating.",
                    )
                    pending.update(externalRef=binding["externalRef"], publicRevision=body["publishedRevision"])
                pending["pendingPayloadRef"] = "pending/" + pending["idempotencyKey"] + ".json"
                write_json(db.root / pending["pendingPayloadRef"], payload)
                metadata["pendingPublication"] = pending
        # No filesystem lock is held during any network request.
        if pending.get("externalRef"):
            ref = public_reference(pending["externalRef"])
            current = await client.request("GET", "/publish/decision/" + ref)
            current_payload = {k: current.get(k) for k in payload}
            if document_hash(current_payload) == pending["payloadHash"]:
                receipt = current
            elif current["revision"] != pending["publicRevision"]:
                with db.transaction() as index:
                    meta = index["recipes"].get(identity, {})
                    if meta.get("pendingPublication", {}).get("idempotencyKey") == pending["idempotencyKey"]:
                        meta.pop("pendingPublication", None)
                        (db.root / pending["pendingPayloadRef"]).unlink(missing_ok=True)
                        if meta.get("publication"):
                            meta["publication"].update(
                                publicRevision=current["revision"],
                                contentHash=current["contentHash"],
                                remoteChanged=True,
                            )
                raise web.HTTPConflict(text="The public recipe changed. Review it before updating.")
            else:
                receipt = await client.request(
                    "PUT", "/publish/decision/" + ref, {**payload, "revision": pending["publicRevision"]}, True
                )
        else:
            receipt = await client.request(
                "POST", "/publish/decision", {**payload, "idempotencyKey": pending["idempotencyKey"]}, True
            )
        with db.transaction() as index:
            db._recipes(index)
            meta = index["recipes"].get(identity)
            if meta and meta.get("pendingPublication", {}).get("idempotencyKey") == pending["idempotencyKey"]:
                meta["publication"] = {
                    k: v for k, v in pending.items() if k not in ("idempotencyKey", "pendingPayloadRef", "payloadHash")
                }
                meta["publication"].update(
                    externalRef=receipt["externalRef"],
                    publishedUrl=receipt["publishedUrl"],
                    publicRevision=receipt["revision"],
                    contentHash=receipt["contentHash"],
                    **{key: payload[key] for key in ("publisherStarred", "publisherRunCount") if key in payload},
                )
                meta.pop("pendingPublication", None)
            (db.root / pending["pendingPayloadRef"]).unlink(missing_ok=True)
        return receipt

    async def unpublish(self, user, db, identity, body):
        client = self.client(user)
        row = db.recipe(identity)
        binding = row.get("publication") if row else None
        require(self.bound(binding, client), "publication", "No share belongs to this publisher account.")
        require(
            body.get("publishedRevision") == binding["publicRevision"],
            "publishedRevision",
            "Refresh the public revision first.",
        )
        await client.request(
            "DELETE",
            "/publish/decision/"
            + public_reference(binding["externalRef"])
            + "?revision="
            + str(body["publishedRevision"]),
            authenticated=True,
        )
        with db.transaction() as index:
            meta = index["recipes"].get(identity, {})
            if meta.get("publication", {}).get("externalRef") == binding["externalRef"]:
                meta.pop("publication", None)
        return {"unpublished": True}

    async def preview(self, user, url):
        config = get_publish_config(self.ctx, user, obscure=False)
        url = import_url(config, url)
        if not getattr(self.ctx.app, "publisher_available", False):
            return await download_recipe(url)
        try:
            ref = reference_from_url(config, url)
        except web.HTTPBadRequest:
            return await download_recipe(url)
        client = self.client(user)
        document = validate_recipe(await client.request("GET", "/d/" + ref + ".json"))
        detail = validate_detail(await client.request("GET", "/publish/decision/" + ref))
        if document_hash(document) != document_hash(detail["document"]):
            raise ConflictError("The public recipe changed. Refresh the preview before importing.")
        detail["document"] = document
        detail["downloadUrl"] = url
        return detail

    async def catalog(self, user, query):
        from urllib.parse import urlencode

        allowed = {k: v for k, v in query.items() if k in ("q", "tag", "user", "skip", "take", "orderBy")}
        try:
            allowed["take"] = min(50, max(1, int(allowed.get("take", 20))))
            allowed["skip"] = min(10000, max(0, int(allowed.get("skip", 0))))
        except (ValueError, TypeError):
            require(False, "pagination", "Use integer pagination values.")
        client = self.client(user)
        return await client.request("GET", "/publish/decisions?" + urlencode(allowed), authenticated=bool(client.config.get("apiKey")))

    async def star(self, user, reference, body):
        require(type(body.get("starred")) is bool, "starred", "Provide true or false.")
        return await self.client(user).request(
            "PUT", "/publish/decision/" + public_reference(reference) + "/star",
            {"starred": body["starred"]}, authenticated=True,
        )

    @staticmethod
    def usage(db, index, identity):
        # Imported examples and queued submissions are not local executions.
        return {
            "publisherStarred": identity in index["favourites"],
            "publisherRunCount": sum(
                1 for _, run in db._runs() if run.get("recipeId") == identity and run.get("status") != "pending"
            ),
        }

    async def tags(self, user):
        return await self.client(user).request("GET", "/publish/decisions/tags")

    async def import_recipe(self, user, db, body):
        client = self.client(user)
        ref = public_reference(body.get("externalRef"))
        detail = await self.preview(user, client.base + "/d/" + ref)
        if detail["revision"] != body.get("publishedRevision") or detail["contentHash"] != body.get("contentHash"):
            raise ConflictError("The public recipe changed. Refresh the preview before importing.")
        filename = recipe_filename(body.get("filename") or detail["filename"])
        example_ref = uuid.uuid4().hex + ".json"
        source = {
            "baseUrl": client.base,
            "externalRef": ref,
            "publishedUrl": client.base + "/d/" + ref,
            "author": detail.get("author"),
            "publicRevision": detail["revision"],
            "contentHash": detail["contentHash"],
            "importedAt": time.time(),
            "exampleRef": example_ref,
        }
        return db.import_shared(detail["document"], filename, body.get("replaceRevision"), source, detail["execution"])
