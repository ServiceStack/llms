import json
import os

from aiohttp import web

g_db = None

try:
    from llms.extensions.gallery.db import GalleryDB
except ImportError as e:
    print(f"Failed to import GalleryDB: {e}")
    GalleryDB = None


def install(ctx):
    ctx.log("!!! gallery.install !!!")

    def get_gallery_db():
        global g_db
        if g_db is None and GalleryDB:
            try:
                db_path = os.path.join(ctx.get_user_path(), "gallery", "gallery.sqlite")
                g_db = GalleryDB(ctx, db_path)
            except Exception as e:
                ctx.err("Failed to init GalleryDB", e)
        return g_db

    if not get_gallery_db():
        return

    def on_cache_save(context):
        url = context["url"]
        info = context["info"]
        ctx.log(f"cache saved: {url}")
        ctx.log(json.dumps(info, indent=2))

        if "url" not in info:
            info["url"] = url
        g_db.insert_media(info)

    ctx.register_cache_saved_filter(on_cache_save)

    async def query_media(request):
        rows = g_db.query_media(request.query, user=ctx.get_username(request))
        return web.json_response(rows)

    ctx.add_get("media", query_media)

    async def media_totals(request):
        rows = g_db.media_totals(user=ctx.get_username(request))
        return web.json_response(rows)

    ctx.add_get("media/totals", media_totals)


__install__ = install
