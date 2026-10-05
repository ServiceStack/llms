"""Decision Studio: independent typed Jev requests, recipes and local history."""

import json
from pathlib import Path

from aiohttp import web

from llms.extensions.jev.client import DecisionClient, provider_status
from llms.extensions.jev.example_names import ExampleNamer
from llms.extensions.jev.execution import Executor
from llms.extensions.jev.generation import generate
from llms.extensions.jev.schema import (
    MAX_BYTES,
    MODELS,
    ValidationError,
    compile_request,
    require,
    text,
    validate_recipe,
)
from llms.extensions.jev.storage import (
    BusyError,
    ConflictError,
    InvalidIdentityError,
    JevStore,
    RecipeExistsError,
    StorageError,
)


def bundled_recipes():
    result = {}
    for path in sorted(Path(__file__).with_name("recipes").glob("*.json")):
        identity = path.stem
        result[identity] = {
            "id": identity,
            "filename": path.name,
            "revision": 1,
            "document": validate_recipe(json.loads(path.read_text())),
        }
    return result


def install(ctx):
    catalog = bundled_recipes()
    executor = Executor(DecisionClient(ctx))
    example_namer = ExampleNamer(ctx)

    def store(request):
        user = ctx.assert_username(request)
        return user, JevStore(ctx.get_user_path(user), catalog)

    async def body(request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            raw.extend(chunk)
            require(len(raw) <= MAX_BYTES, "document", "Keep the request under 512 KB.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ValidationError("document", "Provide valid JSON.") from None
        require(isinstance(value, dict), "document", "Expected a JSON object.")
        return value

    def wrap(handler):
        async def guarded(request):
            try:
                return await handler(request)
            except ValidationError as e:
                return web.json_response(
                    {
                        "responseStatus": {
                            "errorCode": "ValidationError",
                            "message": str(e),
                            "errors": [{"fieldName": e.path, "message": e.message}],
                        }
                    },
                    status=400,
                )
            except (ConflictError, BusyError) as e:
                return web.json_response(
                    {
                        "responseStatus": {
                            "errorCode": type(e).__name__,
                            "message": str(e),
                            **({"existingRecipe": e.recipe} if isinstance(e, RecipeExistsError) else {}),
                        }
                    },
                    status=429 if isinstance(e, BusyError) else 409,
                )
            except StorageError as e:
                return web.json_response(
                    {"responseStatus": {"errorCode": "StorageError", "message": str(e)}}, status=500
                )
            except InvalidIdentityError as e:
                return web.json_response(
                    {
                        "responseStatus": {
                            "errorCode": "ValidationError",
                            "message": str(e),
                            "errors": [{"fieldName": "filename", "message": str(e)}],
                        }
                    },
                    status=400,
                )
            except TimeoutError:
                return web.json_response(
                    {
                        "responseStatus": {
                            "errorCode": "Timeout",
                            "message": "Recipe generation timed out. Try a faster model.",
                        }
                    },
                    status=504,
                )
            except web.HTTPException as e:
                return web.json_response(
                    {"responseStatus": {"errorCode": e.__class__.__name__, "message": e.text or e.reason}},
                    status=e.status,
                )

        return guarded

    def get_recipe(db, identity):
        try:
            recipe = db.recipe(identity)
        except InvalidIdentityError:
            recipe = None
        if not recipe:
            raise web.HTTPNotFound(text="Recipe not found.")
        return recipe

    def get_run(db, identity):
        try:
            run = db.run(identity)
        except InvalidIdentityError:
            run = None
        if not run:
            raise web.HTTPNotFound(text="Decision not found.")
        return run

    async def status(request):
        ctx.assert_username(request)
        provider, message = provider_status(ctx)
        return web.json_response({"available": provider is not None, "message": message, "models": MODELS})

    async def recipes(request):
        _, db = store(request)
        favourites = db.favourites()
        items = [
            {
                "id": row["id"],
                "filename": row["filename"],
                "revision": row["revision"],
                "previousIds": row.get("previousIds", []),
                "favourite": row["id"] in favourites,
                "name": row["document"]["name"],
                "description": row["document"].get("description", ""),
                "tags": row["document"].get("tags", []),
                "content": row["document"].get("content", ""),
                "questionCount": len(row["document"]["questions"]),
            }
            for row in db.recipes()
        ]
        return web.json_response({"items": items})

    async def recipe(request):
        _, db = store(request)
        return web.json_response(get_recipe(db, request.match_info["id"]))

    async def save(request):
        _, db = store(request)
        data = await body(request)
        document = validate_recipe(data.get("document"))
        require(
            data.get("filename") is None or isinstance(data["filename"], str) and bool(data["filename"]),
            "filename",
            "Provide a recipe JSON filename.",
        )
        identity = request.match_info.get("id")
        if identity:
            get_recipe(db, identity)
            require(
                type(data.get("revision")) is int and data["revision"] >= 1,
                "revision",
                "Provide the saved recipe revision.",
            )
        replacement = data.get("replaceRevision")
        require(
            replacement is None or not identity and type(replacement) is int and replacement >= 0,
            "replaceRevision",
            "Provide the recipe revision to replace when importing.",
        )
        return web.json_response(
            db.save(document, identity, data.get("revision"), replacement, data.get("filename")),
            status=200 if identity or replacement is not None else 201,
        )

    async def delete_recipe(request):
        _, db = store(request)
        identity = request.match_info["id"]
        get_recipe(db, identity)
        db.delete_recipe(identity)
        return web.json_response({"deleted": True})

    async def favourite(request):
        _, db = store(request)
        identity = request.match_info["id"]
        get_recipe(db, identity)
        data = await body(request)
        require(isinstance(data.get("enabled"), bool), "enabled", "Use true or false.")
        db.favourite(identity, data["enabled"])
        return web.json_response({"enabled": data["enabled"]})

    async def validate(request):
        ctx.assert_username(request)
        data = await body(request)
        doc = validate_recipe(data.get("recipe"))
        return web.json_response(
            {"recipe": doc, "request": compile_request(doc, data["input"]) if "input" in data else None}
        )

    async def submit(request):
        _, db = store(request)
        data = await body(request)
        text(data.get("submissionId"), "submissionId", 100)
        doc = validate_recipe(data.get("recipe"))
        inputs = data.get("input")
        compiled = compile_request(doc, inputs)
        identity = data.get("recipeId")
        require(
            identity is None or isinstance(identity, str) and 0 < len(identity) <= 240,
            "recipeId",
            "Choose a saved recipe ID or omit it.",
        )
        source = get_recipe(db, identity) if identity else None
        run, created = db.submit(
            data["submissionId"],
            doc,
            inputs,
            compiled,
            executor.owner,
            identity,
            source["revision"] if source else None,
        )
        if created:
            executor.launch(db, run)
        return web.json_response(run, status=202 if created else 200)

    async def runs(request):
        _, db = store(request)
        try:
            limit = max(1, min(int(request.query.get("limit", 20)), 50))
            result = db.history(request.query.get("recipeId"), request.query.get("cursor"), limit)
        except (ConflictError, StorageError):
            raise
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response(result)

    async def run(request):
        _, db = store(request)
        return web.json_response(get_run(db, request.match_info["id"]))

    async def cancel(request):
        _, db = store(request)
        identity = request.match_info["id"]
        get_run(db, identity)
        return web.json_response(executor.cancel(db, identity))

    async def delete_run(request):
        _, db = store(request)
        identity = request.match_info.get("id")
        if identity:
            get_run(db, identity)
        return web.json_response({"deleted": db.delete_run(identity)})

    async def author(request):
        user = ctx.assert_username(request)
        return web.json_response(await generate(ctx, user, await body(request), request.path.endswith("/improve")))

    async def example_name(request):
        user, db = store(request)
        return web.json_response(await example_namer.suggest(get_run(db, request.match_info["id"]), user))

    for method, path, handler in [
        ("get", "status", status),
        ("get", "recipes", recipes),
        ("get", "recipes/{id}", recipe),
        ("post", "recipes", save),
        ("put", "recipes/{id}", save),
        ("delete", "recipes/{id}", delete_recipe),
        ("put", "favourites/{id}", favourite),
        ("post", "validate", validate),
        ("post", "runs", submit),
        ("get", "runs", runs),
        ("get", "runs/{id}", run),
        ("post", "runs/{id}/cancel", cancel),
        ("post", "runs/{id}/example-name", example_name),
        ("delete", "runs/{id}", delete_run),
        ("delete", "history", delete_run),
        ("post", "generate", author),
        ("post", "improve", author),
    ]:
        getattr(ctx, "add_" + method)(path, wrap(handler))
    from llms.extensions.jev.sharing import Sharing

    sharing = Sharing(ctx)

    async def share_status(request):
        user, db = store(request)
        return web.json_response(await sharing.status(user, db, request.match_info["id"]))

    async def share_write(request):
        user, db = store(request)
        operation = sharing.unpublish if request.method == "DELETE" else sharing.publish
        return web.json_response(await operation(user, db, request.match_info["id"], await body(request)))

    async def shared_catalog(request):
        user, _ = store(request)
        return web.json_response(await sharing.catalog(user, request.query))

    async def shared_tags(request):
        user, _ = store(request)
        return web.json_response(await sharing.tags(user))

    async def shared_preview(request):
        user, _ = store(request)
        value = await body(request)
        return web.json_response(await sharing.preview(user, value.get("url")))

    async def shared_import(request):
        user, db = store(request)
        return web.json_response(await sharing.import_recipe(user, db, await body(request)))

    async def shared_star(request):
        user, _ = store(request)
        return web.json_response(await sharing.star(user, request.match_info["reference"], await body(request)))

    for method, path, handler in [
        ("get", "recipes/{id}/share", share_status),
        ("post", "recipes/{id}/share", share_write),
        ("delete", "recipes/{id}/share", share_write),
        ("get", "shared-recipes", shared_catalog),
        ("put", "shared-recipes/{reference}/star", shared_star),
        ("get", "tags", shared_tags),
        ("post", "recipes/preview-url", shared_preview),
        ("post", "shared-recipes/preview", shared_preview),
        ("post", "shared-recipes/import", shared_import),
    ]:
        getattr(ctx, "add_" + method)(path, wrap(handler))

    ctx.register_cleanup_handler(executor.close)


__install__ = install
