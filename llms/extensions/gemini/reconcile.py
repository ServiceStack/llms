"""Repair remote copies by document identity without racing the upload worker."""
import asyncio
import json
import os
import threading
from collections import defaultdict
from datetime import datetime

from . import db as db_module

_locks = {}
_locks_guard = threading.Lock()


def document_lock(ctx, doc_id):
    with _locks_guard:
        return _locks.setdefault((id(ctx), doc_id), threading.Lock())


def metadata_identity(remote):
    values = {item.key: item for item in remote.custom_metadata or []}
    identity = values.get("id")
    content = values.get("hash")
    return (int(identity.numeric_value) if identity and identity.numeric_value is not None else None,
            content.string_value if content else None)


def remote_fields(remote, db):
    def timestamp(value):
        return value.isoformat(" ") if hasattr(value, "isoformat") else value
    return {"name": remote.name, "displayName": remote.display_name, "state": remote.state,
            "sizeBytes": remote.size_bytes, "mimeType": remote.mime_type,
            "createTime": timestamp(remote.create_time), "updateTime": timestamp(remote.update_time),
            "customMetadata": db.custom_metadata_dto(remote.custom_metadata)}


def pending_names(document):
    value = document.get("pendingDeleteNames") or []
    return json.loads(value) if isinstance(value, str) else list(value)


async def repair_documents(ctx, db, client, local_docs, remote_docs, user):
    result = {name: [] for name in ("local_missing", "remote_missing", "pending_uploads",
                                   "missing_metadata", "metadata_mismatch", "unmatched",
                                   "duplicate_docs", "errors")}
    result.update(matched=0, removed=0)
    by_id = {d["id"]: d for d in local_docs}
    hashes, names = defaultdict(list), defaultdict(list)
    for doc in local_docs:
        if doc.get("hash"):
            hashes[doc["hash"]].append(doc)
        if doc.get("name"):
            names[doc["name"]].append(doc)
    groups = defaultdict(list)
    for remote in remote_docs:
        remote_id, remote_hash = metadata_identity(remote)
        local = by_id.get(remote_id)
        if local is None and len(names[remote.name]) == 1:
            local = names[remote.name][0]
        if local is None and remote_id is None and remote_hash and len(hashes[remote_hash]) == 1:
            local = hashes[remote_hash][0]
        if local is None:
            result["local_missing"].append(remote)
        else:
            groups[local["id"]].append(remote)

    limit = asyncio.Semaphore(4)
    for snapshot in local_docs:
        lock = document_lock(ctx, snapshot["id"])
        if not lock.acquire(blocking=False):
            if not snapshot.get("uploadedAt"):
                result["pending_uploads"].append(snapshot)
            continue
        try:
            local = db.get_document(snapshot["id"], user=user)
            if not local or local.get("tombstonedAt"):
                continue
            if local.get("updatedAt") != snapshot.get("updatedAt"):
                if not local.get("uploadedAt") and not local.get("error"):
                    result["pending_uploads"].append(local)
                continue
            copies = groups[local["id"]]
            label = f"{local.get('category') or ''}/{local.get('displayName') or local['id']}"

            def owned(remote):
                return metadata_identity(remote)[0] == local["id"] or remote.name == snapshot.get("name")

            def current(remote):
                remote_id, remote_hash = metadata_identity(remote)
                return (remote_id == local["id"] and remote_hash == local.get("hash")
                        and remote.display_name == local.get("displayName")
                        and not db_module.metadata_differs({**local,
                            "customMetadata": db.custom_metadata_dto(remote.custom_metadata)}))

            valid = [r for r in copies if r.state == "STATE_ACTIVE" and current(r)]
            valid.sort(key=lambda r: (r.name == local.get("name"), str(r.create_time or ""), r.name or ""), reverse=True)
            keep = valid[0] if valid else None
            if keep:
                fields = remote_fields(keep, db)
                stored = dict(local)
                if isinstance(stored.get("customMetadata"), str):
                    try:
                        stored["customMetadata"] = json.loads(stored["customMetadata"])
                    except ValueError:
                        pass
                if any(stored.get(k) != v for k, v in fields.items()):
                    result["unmatched"].append({"doc": keep, "local": local})
                extras = list(dict.fromkeys([r.name for r in copies if r.name and r.name != keep.name and owned(r)]
                                             + pending_names(local)))
                extras = [name for name in extras if name != keep.name]
                patch = {**fields, "uploadedAt": local.get("uploadedAt") or datetime.now(),
                         "startedAt": None, "error": None, "uploadOperationName": None,
                         "uploadOperationHash": None, "pendingDeleteNames": extras or None}
                await db.update_document_async(local["id"], patch, user=user)
                result["matched"] += 1

                async def remove(name):
                    async with limit:
                        try:
                            await asyncio.to_thread(client.file_search_stores.documents.delete,
                                                    name=name, config={"force": True})
                            return name, None
                        except Exception as error:
                            if getattr(error, "status", None) in (404, 410):
                                return name, None
                            return name, ctx.error_message(error)

                deleted = await asyncio.gather(*(remove(name) for name in extras))
                remaining = [name for name, error in deleted if error]
                result["removed"] += sum(error is None for _, error in deleted)
                result["errors"].extend(f"{label}: could not remove {name}: {error}"
                                        for name, error in deleted if error)
                ambiguous = any(r.name != keep.name and not owned(r) for r in copies)
                if ambiguous:
                    result["errors"].append(f"{label}: a remote copy has ambiguous identity; it was preserved.")
                if remaining or ambiguous:
                    result["duplicate_docs"].append(local)
                await db.update_document_async(local["id"], {
                    "pendingDeleteNames": remaining or None,
                    "state": "DUPLICATE_FILE" if remaining or ambiguous else keep.state}, user=user)
                continue

            if not copies:
                if local.get("uploadedAt") or local.get("error"):
                    result["remote_missing"].append(local)
                state = "MISSING_FROM_REMOTE"
            elif any(any(v is None for v in metadata_identity(r)) for r in copies):
                result["missing_metadata"].append({"doc": copies[0], "local": local})
                state = "MISSING_METADATA"
            elif any(not current(r) for r in copies):
                result["metadata_mismatch"].append({"doc": copies[0], "local": local})
                state = "METADATA_MISMATCH"
            else:
                state = "STATE_FAILED" if any(r.state == "STATE_FAILED" for r in copies) else "STATE_PENDING"
            url = local.get("url") or ""
            if not (url.startswith("/~cache/") and os.path.isfile(ctx.get_cache_path(url[len("/~cache/"):]))):
                if len(copies) > 1:
                    state = "DUPLICATE_FILE"
                    result["duplicate_docs"].append(local)
                result["errors"].append(f"{label}: no healthy current remote copy and the local cached file is missing.")
                await db.update_document_async(local["id"], {"state": state}, user=user)
                continue
            fallback = sorted([r for r in copies if owned(r) and r.state == "STATE_ACTIVE"],
                              key=lambda r: (r.name == local.get("name"), str(r.create_time or "")), reverse=True)
            fallback = fallback[0] if fallback else next((r for r in copies if owned(r) and r.name == local.get("name")), None)
            name = fallback.name if fallback else None
            cleanup = list(dict.fromkeys([r.name for r in copies if owned(r) and r.name and r.name != name]
                                          + pending_names(local)))
            patch = {"name": name, "state": state, "pendingDeleteNames": cleanup or None,
                     "uploadedAt": None, "startedAt": None, "error": None}
            desired = [r for r in copies if metadata_identity(r)[1] == local.get("hash")]
            if desired and all(r.state == "STATE_FAILED" for r in desired):
                patch.update(uploadOperationName=None, uploadOperationHash=None)
            await db.update_document_async(local["id"], patch, user=user)
            result["pending_uploads"].append(local)
        finally:
            lock.release()
    return result
