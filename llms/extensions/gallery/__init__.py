import json
import os

from aiohttp import web

from .db import GalleryDB

g_db = None


def install(ctx):
    def get_db():
        global g_db
        if g_db is None and GalleryDB:
            try:
                db_path = os.path.join(ctx.get_user_path(), "gallery", "gallery.sqlite")
                g_db = GalleryDB(ctx, db_path)
                ctx.register_shutdown_handler(g_db.db.close)
            except Exception as e:
                ctx.err("Failed to init GalleryDB", e)
        return g_db

    if not get_db():
        return

    def media_dto(row):
        return row and g_db.to_dto(row, ["reactions", "category", "tags", "ratings", "objects", "metadata"])

    def on_cache_save(context):
        url = context.get("url", None)
        info = context.get("info", {})
        user = context.get("user", None)
        ctx.log(f"cache saved: {url}")
        ctx.dbg(json.dumps(info, indent=2))

        if "url" not in info:
            info["url"] = url
        g_db.insert_media(info, user=user)

    ctx.register_cache_saved_filter(on_cache_save)

    async def query_media(request):
        rows = g_db.query_media(request.query, user=ctx.get_username(request))
        dtos = [media_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("media", query_media)

    async def media_totals(request):
        rows = g_db.media_totals(user=ctx.get_username(request))
        return web.json_response(rows)

    ctx.add_get("media/totals", media_totals)

    async def delete_media(request):
        hash = request.match_info["hash"]
        g_db.delete_media(hash, user=ctx.get_username(request))
        return web.json_response({})

    ctx.add_delete("media/{hash}", delete_media)


__install__ = install
