import json
import os
import sqlite3
from typing import Any, Dict


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
        self.columns = {
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
            "variant_id": "TEXT",  # 1
            "variant_name": "TEXT",  # 4x Upscaled
            "published": "TIMESTAMP",
            "metadata": "JSON",  # {"date":1767111852}
        }

        ratios = ctx.aspect_ratios.keys()

        self.formats = {
            "square": [ratio for ratio in ratios if ratio_format(ratio) == 0],
            "landscape": [ratio for ratio in ratios if ratio_format(ratio) == 1],
            "portrait": [ratio for ratio in ratios if ratio_format(ratio) == -1],
        }
        self.init_db()

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
        return sqlite3.connect(self.db_path)

    def exec(self, conn, sql, parameters=None):
        self.ctx.dbg("SQL>" + ("\n" if "\n" in sql else " ") + sql)
        return conn.execute(sql, parameters or ())

    def all(self, conn, sql, parameters=None):
        conn.row_factory = sqlite3.Row
        cursor = self.exec(conn, sql, parameters)
        return [dict(row) for row in cursor.fetchall()]

    def init_db(self):
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with self.get_connection() as conn:
            # Create table with all columns
            self.exec(
                conn,
                """
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    type TEXT NOT NULL,
                    prompt TEXT,
                    model TEXT,
                    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cost REAL,
                    seed INTEGER,
                    url TEXT NOT NULL UNIQUE,
                    hash TEXT NOT NULL UNIQUE,
                    aspect_ratio TEXT,
                    width INTEGER,
                    height INTEGER,
                    size INTEGER,
                    duration INTEGER,
                    user TEXT,
                    reactions JSON,
                    caption TEXT,
                    description TEXT,
                    phash TEXT,
                    color TEXT,
                    category JSON,
                    tags JSON,
                    rating TEXT,
                    ratings JSON,
                    objects JSON,
                    variant_id TEXT,
                    variant_name TEXT,
                    published TIMESTAMP,
                    metadata JSON
                )
                """,
            )

            self.exec(conn, "CREATE INDEX IF NOT EXISTS idx_media_user ON media(user)")
            self.exec(conn, "CREATE INDEX IF NOT EXISTS idx_media_type ON media(type)")

            # Check for missing columns and migrate if necessary
            cur = self.exec(conn, "PRAGMA table_info(media)")
            columns = {row[1] for row in cur.fetchall()}

            for col, dtype in self.columns.items():
                if col not in columns:
                    try:
                        self.exec(conn, f"ALTER TABLE media ADD COLUMN {col} {dtype}")
                    except Exception as e:
                        self.ctx.err(f"adding column {col}", e)

    def insert_media(self, info):
        try:
            if not info:
                raise Exception("info is required")

            # Helper to safely dump JSON if value exists
            def db_value(val):
                if val is None or val == "":
                    return None
                if isinstance(val, (dict, list)):
                    return json.dumps(val)
                return val

            meta = {}
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
                        meta[k] = parts[0]
                else:
                    meta[k] = db_value(val)
            # for items not in known_columns, add to metadata
            for k in info:
                if k not in known_columns:
                    metadata[k] = info[k]

            if not meta.get("hash"):
                meta["hash"] = meta["url"].split("/")[-1].split(".")[0]

            if "width" in meta and "height" in meta:
                meta["aspect_ratio"] = self.closest_aspect_ratio(int(meta["width"]), int(meta["height"]))

            meta["metadata"] = db_value(metadata)

            with self.get_connection() as conn:
                insert_keys = list(meta.keys())
                insert_body = ", ".join(insert_keys)
                insert_values = ", ".join(["?" for _ in insert_keys])

                self.exec(
                    conn,
                    f"""
                    INSERT INTO media (
                        {insert_body}
                    )
                    VALUES ({insert_values})
                    """,
                    tuple(meta[k] for k in insert_keys),
                )
        except sqlite3.IntegrityError as e:
            # unique constraint failed, file already exists.
            self.ctx.dbg(f"media already exists {e}")
        except Exception as e:
            self.ctx.err("insert media", e)

    def get_user_filter(self, user=None):
        if user is None:
            return "WHERE user IS NULL ", {}
        else:
            return "WHERE user = :user ", {"user": user}

    def media_totals(self, user=None):
        try:
            with self.get_connection() as conn:
                sql_where, params = self.get_user_filter(user)
                return self.all(
                    conn,
                    f"SELECT type, COUNT(*) as count FROM media {sql_where} GROUP BY type ORDER BY count DESC",
                    params,
                )
        except Exception as e:
            self.ctx.err("media_totals", e)
            return []

    def all_media(self, limit=100, offset=0, user=None):
        try:
            with self.get_connection() as conn:
                sql_where, params = self.get_user_filter(user)
                params.update({"limit": limit, "offset": offset})
                return self.all(
                    conn,
                    f"SELECT * FROM media {sql_where} ORDER BY id DESC LIMIT :limit OFFSET :offset",
                    params,
                )
        except Exception as e:
            self.ctx.err(f"all_media ({limit}, {offset})", e)
            return []

    def query_media(self, query: Dict[str, Any], user=None):
        try:
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

            sql_orderby = "ORDER BY " + sort
            sql_orderby = sql_orderby[1:] + " DESC" if sql_orderby.startswith("-") else sql_orderby + " ASC"

            with self.get_connection() as conn:
                return self.all(
                    conn,
                    f"SELECT * FROM media {sql_where} {sql_orderby} LIMIT :take OFFSET :skip",
                    params,
                )
        except Exception as e:
            self.ctx.err(f"query_media ({take}, {skip})", e)
            return []
