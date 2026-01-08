import datetime
import os
from typing import Any, Dict

from llms.db import DbManager, order_by, to_dto


def with_user(data, user):
    if user is None:
        if "user" in data:
            del data["user"]
        return data
    else:
        data["user"] = user
        return data


def ratio_format(ratio):
    w, h = ratio.split(":")
    if int(w) < int(h):
        return -1
    if int(w) > int(h):
        return 1
    return 0


class GalleryDB:
    def __init__(self, ctx, db_path=None):
        if db_path is None:
            raise Exception("db_path is required")

        self.ctx = ctx
        self.db_path = str(db_path)
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self.db = DbManager(ctx, self.db_path)
        self.columns = {
            "id": "INTEGER",
            "name": "TEXT",  # chutes-hunyuan-image-3.png (filename)
            "type": "TEXT",  # image|audio|video
            "prompt": "TEXT",
            "model": "TEXT",  # gemini-2.5-flash-image
            "created": "TIMESTAMP",
            "cost": "REAL",  # 0.03836745
            "seed": "INTEGER",  # 1
            "url": "TEXT",  # /~cache/23/238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef.png
            "hash": "TEXT",  # 238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef
            "aspect_ratio": "TEXT",  # 9:16
            "width": "INTEGER",  # 768
            "height": "INTEGER",  # 1344
            "size": "INTEGER",  # 1593817 (bytes)
            "duration": "INTEGER",  # 100 (secs)
            "user": "TEXT",
            "reactions": "JSON",  # {"❤": 1, "👍": 2}
            "caption": "TEXT",
            "description": "TEXT",
            "phash": "TEXT",  # 95482f9e1c3f63a1
            "color": "TEXT",  # #040609
            "category": "JSON",  # {"fantasy": 0.216552734375, "game character": 0.282470703125}
            "tags": "JSON",  # {"bug": 0.9706085920333862, "mask": 0.9348311424255371, "glowing": 0.8394700884819031}
            "rating": "TEXT",  # "M"
            "ratings": "JSON",  # {"predicted_rating":"G","confidence":0.2164306640625,"all_scores":{"G":0.2164306640625,"PG":0.21240234375,"PG-13":0.1915283203125,"M":0.2069091796875,"R":0.2064208984375}}
            "objects": "JSON",  # [{"model":"640m","class":"FACE_FEMALE","score":0.5220243334770203,"box":[361,346,367,451]},{"model":"640m","class":"FEMALE_BREAST_EXPOSED","score":0.31755316257476807,"box":[672,1068,212,272]}]
            "variantId": "TEXT",  # 1
            "variantName": "TEXT",  # 4x Upscaled
            "published": "TIMESTAMP",
            "metadata": "JSON",  # {"date":1767111852}
        }

        ratios = ctx.aspect_ratios.keys()

        self.formats = {
            "square": [ratio for ratio in ratios if ratio_format(ratio) == 0],
            "landscape": [ratio for ratio in ratios if ratio_format(ratio) == 1],
            "portrait": [ratio for ratio in ratios if ratio_format(ratio) == -1],
        }
        with self.db.create_writer_connection() as conn:
            self.init_db(conn)

    def closest_aspect_ratio(self, width, height):
        target_ratio = width / height
        closest_ratio = "1:1"
        min_diff = float("inf")

        for ratio in self.ctx.aspect_ratios:
            w, h = ratio.split(":")
            diff = abs(target_ratio - (int(w) / int(h)))
            if diff < min_diff:
                min_diff = diff
                closest_ratio = ratio

        return closest_ratio

    def get_connection(self):
        return self.db.create_reader_connection()

    def init_db(self, conn):
        # Create table with all columns
        overrides = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "created": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }
        sql_columns = ",".join([f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns.items()])
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS media (
                {sql_columns}
            )
            """,
        )

        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_media_user ON media(user)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_media_type ON media(type)")

        # Check for missing columns and migrate if necessary
        cur = self.db.exec(conn, "PRAGMA table_info(media)")
        columns = {row[1] for row in cur.fetchall()}

        for col, dtype in self.columns.items():
            if col not in columns:
                try:
                    self.db.exec(conn, f"ALTER TABLE media ADD COLUMN {col} {dtype}")
                except Exception as e:
                    self.ctx.err(f"adding column {col}", e)

    def to_dto(self, row, json_columns):
        return to_dto(self.ctx, row, json_columns)

    def get_user_filter(self, user=None, params=None):
        if user is None:
            return "WHERE user IS NULL", params or {}
        else:
            args = params.copy() if params else {}
            args.update({"user": user})
            return "WHERE user = :user", args

    def prepare_media(self, media, id=None, user=None):
        now = datetime.now()
        if id:
            media["id"] = id
        else:
            media["created"] = now
        return with_user(media, user=user)

    def insert_media(self, info, user=None, callback=None):
        if not info:
            raise Exception("info is required")

        media = {}
        metadata = {}
        known_columns = self.columns.keys()
        for k in known_columns:
            val = info.get(k, None)
            if k == "metadata":
                continue
            if k == "created" and not val:
                continue
            if k == "type":
                parts = val.split("/")
                if parts[0] == "image" or parts[0] == "video" or parts[0] == "audio":
                    media[k] = parts[0]
            else:
                media[k] = self.db.value(val)
        # for items not in known_columns, add to metadata
        for k in info:
            if k not in known_columns:
                metadata[k] = info[k]

        if not media.get("hash"):
            media["hash"] = media["url"].split("/")[-1].split(".")[0]

        if "width" in media and "height" in media and media["width"] and media["height"]:
            media["aspect_ratio"] = self.closest_aspect_ratio(int(media["width"]), int(media["height"]))

        media["metadata"] = self.db.value(metadata)
        media = with_user(media, user=user)

        insert_keys = list(media.keys())
        insert_body = ", ".join(insert_keys)
        insert_values = ", ".join(["?" for _ in insert_keys])

        sql = f"INSERT INTO media ({insert_body}) VALUES ({insert_values})"

        self.db.write(sql, tuple(media[k] for k in insert_keys), callback)

    def media_totals(self, user=None):
        sql_where, params = self.get_user_filter(user)
        return self.db.all(
            f"SELECT type, COUNT(*) as count FROM media {sql_where} GROUP BY type ORDER BY count DESC",
            params,
        )

    def query_media(self, query: Dict[str, Any], user=None):
        try:
            all_columns = self.columns.keys()
            take = query.get("take", 50)
            skip = query.get("skip", 0)
            sort = query.get("sort", "-id")

            # always filter by user
            sql_where, params = self.get_user_filter(user)
            params.update(
                {
                    "take": take,
                    "skip": skip,
                }
            )

            filter = {}
            for k in query:
                if k in self.columns:
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(prompt LIKE :q OR name LIKE :q OR description LIKE :q OR caption LIKE :q)"
                params["q"] = f"%{query['q']}%"

            if "format" in query:
                sql_where += " AND " if sql_where else "WHERE "
                format_ratios = self.formats.get(query["format"], [])
                ratios = ", ".join([f"'{ratio}'" for ratio in format_ratios])
                sql_where += f"aspect_ratio IN ({ratios})"

            return self.db.all(
                f"SELECT * FROM media {sql_where} {order_by(all_columns, sort)} LIMIT :take OFFSET :skip",
                params,
            )
        except Exception as e:
            self.ctx.err(f"query_media ({take}, {skip})", e)
            return []

    def delete_media(self, hash, user=None, callback=None):
        sql_where, params = self.get_user_filter(user)
        params.update({"hash": hash})
        self.db.write(f"DELETE FROM media {sql_where} AND hash = :hash", params, callback)
