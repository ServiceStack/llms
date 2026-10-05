"""Per-user portable JSON recipes and immutable decision snapshots.

A short cross-process lock serializes revisions/submissions. Individual files are
atomically replaced; recipe hashes recover metadata after an interrupted write.
SQLite is read only to migrate the earlier store, never used for new data.
"""

import hashlib
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ACTIVE = ("pending", "running")
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


class ConflictError(ValueError):
    pass


class RecipeExistsError(ConflictError):
    def __init__(self, row):
        self.recipe = {"id": row["id"], "revision": row["revision"]}
        super().__init__(f'The recipe "{row["id"]}" already exists. Replacing it will clear its history.')


class BusyError(ValueError):
    pass


class InvalidIdentityError(ValueError):
    pass


class StorageError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def document_hash(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def recipe_identity(name):
    # Names are literal, portable filenames, including spaces and Unicode.
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 120
        or len(name.encode("utf-8")) > 240
        or name != name.strip()
        or name.endswith(".")
        or name.casefold() == "_drafts"
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)
    ):
        raise InvalidIdentityError(
            "Use a recipe name that is a valid filename, without slashes or reserved characters."
        )
    return name


def identity_path(identity):
    if not isinstance(identity, str):
        raise InvalidIdentityError("Invalid decision ID.")
    if not IDENTITY.fullmatch(identity):
        prefix, separator, number = identity.rpartition("-")
        if not separator or not re.fullmatch(r"[0-9]{5,}", number) or len((identity + ".md").encode("utf-8")) > 255:
            raise InvalidIdentityError("Invalid decision ID.")
        recipe_reference(prefix)
    return identity


def recipe_filename(filename):
    if (
        not isinstance(filename, str)
        or not filename.lower().endswith(".json")
        or len(filename.encode("utf-8")) > 245
        or not filename[:-5].strip()
        or filename != filename.strip()
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", filename)
    ):
        raise InvalidIdentityError("Use a recipe JSON filename, such as sentiment.json.")
    recipe_reference(filename[:-5])
    return filename


def recipe_reference(reference):
    # References are the literal filename stem, with only the final .json removed.
    if (
        not isinstance(reference, str)
        or not reference.strip()
        or reference != reference.strip()
        or len(reference.encode("utf-8")) > 240
        or reference in (".", "..")
        or reference.endswith(".")
        or reference.casefold() == "_drafts"
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', reference)
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", reference)
    ):
        raise InvalidIdentityError("Use a portable recipe filename stem, such as sentiment.")
    return reference


def default_filename(name):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().rstrip(".") or "recipe"
    while len(stem.encode("utf-8")) > 240:
        stem = stem[:-1]
    try:
        return recipe_filename(stem + ".json")
    except InvalidIdentityError:
        return "recipe.json"


def same_path(left, right):
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError):
        raise StorageError("A saved Jev JSON file is invalid. Restore it from a backup or correct its JSON.") from None


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".jev-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


RECORD_MARKER = "\n<!-- jev-record -->\n```json\n"


def read_history(path):
    if path.suffix.lower() == ".json":
        return read_json(path)
    try:
        content = path.read_text(encoding="utf-8")
        record = content.rsplit(RECORD_MARKER, 1)[1]
        if not record.endswith("\n```\n"):
            raise ValueError()
        return json.loads(record[:-5])
    except (ValueError, IndexError, UnicodeError):
        raise StorageError(
            "A saved Jev history record is invalid. Restore it from a backup or correct its record JSON."
        ) from None


def write_history(path, row):
    if path.suffix.lower() == ".json":
        return write_json(path, row)

    def pretty(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)

    name = row["recipe"]["name"].replace("\n", " ").replace("\r", " ")
    created = datetime.fromtimestamp(row["createdAt"], UTC).isoformat(timespec="seconds")
    content = (
        f"# {row['id']}\n\n"
        f"- Recipe: {name}\n- Status: {row['status']}\n- Created: {created}\n"
        f"- Model: {row['request']['model']}\n\n"
        f"## Input\n\n```json\n{pretty(row['input'])}\n```\n\n"
        f"## Results\n\n```json\n{pretty(row['answers'])}\n```\n\n"
        "## Full record\n" + RECORD_MARKER + pretty(row) + "\n```\n"
    )
    write_text(path, content)


class JevStore:
    def __init__(self, user_path, templates=None):
        self.migration_path = Path(user_path) / ".jev-initialized.json"
        self.root = Path(user_path) / "jev"
        self.path = str(self.root)
        self.recipe_dir = self.root / "recipes"
        self.history_dir = self.root / "history"
        self.index_path = self.root / "index.json"
        self.rename_path = self.root / ".renames.json"
        self.reference_path = self.root / ".references.json"
        self.root.mkdir(parents=True, exist_ok=True)
        with self.transaction() as index:
            self._recipes(index)
            if not index.get("initialized"):
                if not self.migration_path.exists():
                    self.migrate(Path(user_path) / "jev.sqlite", index, templates or {})
                if templates and "sentiment" in templates:
                    # Automatic setup is idempotent and never replaces recipes or clears history.
                    rows = self._recipes(index)
                    if not any(
                        row.get("templateId") in ("01-sentiment", "sentiment.json", "sentiment") for row in rows
                    ):
                        self._save(
                            index,
                            templates["sentiment"]["document"],
                            filename="sentiment.json",
                            template_id="sentiment",
                            initializing=True,
                        )
                index["initialized"] = True
        # This receipt survives deleting jev/. Legacy SQLite is a backup, not a reset source.
        # Write only after initialization commits, so interrupted migrations can retry.
        if not self.migration_path.exists():
            write_json(self.migration_path, {"version": 1})

    @contextmanager
    def transaction(self, wait=False):
        with (self.root / ".lock").open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                if not stream.tell():
                    stream.write(b"0")
                    stream.flush()

                def acquire():
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

                lock_error = OSError
            else:
                import fcntl

                def acquire():
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

                lock_error = BlockingIOError
            deadline = time.monotonic() + (5 if wait else 0)
            while True:
                try:
                    acquire()
                    break
                except lock_error:
                    if time.monotonic() >= deadline:
                        raise ConflictError("Another Jev operation is in progress. Try again shortly.") from None
                    # Internal run transitions must survive another worker's brief
                    # write without losing a provider response or orphaning a run.
                    time.sleep(0.01)
            try:
                index = read_json(self.index_path, {"recipes": {}, "favourites": []})
                if (
                    not isinstance(index, dict)
                    or not isinstance(index.get("recipes"), dict)
                    or not isinstance(index.get("favourites"), list)
                ):
                    raise StorageError("The saved Jev index is invalid. Restore its JSON from a backup.")
                original = encode(index)
                shared_import = read_json(self.root / ".shared-import.json")
                if shared_import:
                    self._apply_shared_import(index, shared_import)
                for old, pending in read_json(self.rename_path, {}).items():
                    new = recipe_identity(pending["document"]["name"])
                    self._rename(index, old, new, pending["document"])
                    index["recipes"][new] = pending["metadata"]
                    old_path = self.recipe_dir / (old + ".json")
                    if not same_path(old_path, self.recipe_dir / (new + ".json")):
                        old_path.unlink(missing_ok=True)
                yield index
                if encode(index) != original:
                    write_json(self.index_path, index)
                self.rename_path.unlink(missing_ok=True)
                (self.root / ".shared-import.json").unlink(missing_ok=True)
                if index.get("filenameStemIdentities"):
                    self.reference_path.unlink(missing_ok=True)
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _recipes(self, index):
        from .schema import validate_recipe

        rows, mapping = [], {}
        old_metadata = dict(index["recipes"])
        migrating = not index.get("filenameStemIdentities")
        for path in self.recipe_dir.iterdir() if self.recipe_dir.exists() else []:
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            filename = recipe_filename(path.name)
            identity = recipe_reference(filename[:-5])
            if any(row["id"].casefold() == identity.casefold() for row in rows):
                raise ConflictError(f'The recipe file "{identity}" already exists with different casing.')
            document = validate_recipe(read_json(path))
            fingerprint = document_hash(document)
            metadata = (
                old_metadata.get(filename)
                if migrating and index.get("filenameIdentities")
                else old_metadata.get(identity)
            )
            if metadata is None:
                metadata = {
                    "revision": 1,
                    "createdAt": path.stat().st_mtime,
                    "updatedAt": path.stat().st_mtime,
                    "hash": fingerprint,
                }
            elif metadata["hash"] != fingerprint:
                metadata.update(revision=metadata["revision"] + 1, updatedAt=time.time(), hash=fingerprint)
            if migrating:
                if index.get("filenameIdentities"):
                    mapping[filename] = identity
                metadata["previousIds"] = list(
                    dict.fromkeys(key for key in [*metadata.get("previousIds", []), filename] if key != identity)
                )
            index["recipes"][identity] = metadata
            rows.append(
                {
                    "id": identity,
                    "filename": filename,
                    "document": document,
                    **{k: v for k, v in metadata.items() if k not in ("hash", "pendingPublication")},
                }
            )
        if migrating:
            index["favourites"] = [mapping.get(key, key) for key in index["favourites"]]
            runs = self._runs()
            references = read_json(self.reference_path)
            if references is None:
                references = {
                    row["id"]: recipe_reference(
                        mapping.get(
                            old, old[:-5] if index.get("filenameIdentities") and old.lower().endswith(".json") else old
                        )
                    )
                    for _, row in runs
                    if (old := row["recipeId"])
                }
                if references:
                    # Keep migration retries unambiguous for names such as foo.json.json.
                    write_json(self.reference_path, references)
            for path, row in runs:
                old = row["recipeId"]
                if not old:
                    continue
                identity = recipe_reference(references[row["id"]])
                row["recipeId"] = identity
                target = self.history_dir / identity / path.name
                write_history(target, row)
                if not same_path(target, path):
                    path.unlink()
                    if not any(path.parent.iterdir()):
                        path.parent.rmdir()
            index["filenameStemIdentities"] = True
        existing = {row["id"] for row in rows}
        index["recipes"] = {key: value for key, value in index["recipes"].items() if key in existing}
        index["favourites"] = [key for key in index["favourites"] if key in existing]
        return sorted(rows, key=lambda row: (-row["updatedAt"], row["id"]))

    def _rename(self, index, old, new, document):
        # Finish any rename journal left by the earlier name-based store.
        recipe_identity(old)
        write_json(self.recipe_dir / (new + ".json"), document)
        for path, row in self._runs():
            if row["recipeId"] == old:
                row["recipeId"] = new
                target = self.history_dir / new / path.name
                write_history(target, row)
                if not same_path(target, path):
                    path.unlink()
        directory = self.history_dir / old
        if not same_path(directory, self.history_dir / new) and directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        index["favourites"] = [new if key == old else key for key in index["favourites"]]
        index["recipes"].pop(old, None)

    def recipes(self):
        with self.transaction() as index:
            return self._recipes(index)

    def recipe(self, identity):
        recipe_reference(identity)
        row = next((row for row in self.recipes() if row["id"] == identity), None)
        if row and row.get("importSource"):
            ref = row["importSource"].get("exampleRef", "")
            if re.fullmatch(r"[a-f0-9]{32}\.json", ref):
                snapshot = read_json(self.root / "source-examples" / ref)
                if snapshot and "execution" in snapshot and "document" in snapshot:
                    row["sourceExample"] = snapshot["execution"]
                    row["sourceDocument"] = snapshot["document"]
                else:
                    row["sourceExample"] = snapshot
                    row["sourceDocument"] = row["document"]
        return row

    def _save(
        self,
        index,
        document,
        identity=None,
        revision=None,
        template_id=None,
        replace_revision=None,
        filename=None,
        initializing=False,
        dry_run=False,
    ):
        rows = self._recipes(index)
        if identity:
            recipe_reference(identity)
            existing = next((row for row in rows if row["id"] == identity), None)
            if not existing or existing["revision"] != revision:
                raise ConflictError(
                    "This recipe changed in another tab or on disk. Reload it or save your edits as a copy."
                )
        else:
            filename = recipe_filename(filename or default_filename(document["name"]))
            identity = recipe_reference(filename[:-5])
            existing = next((row for row in rows if row["id"].casefold() == identity.casefold()), None)
            if existing:
                if initializing:
                    return existing
                if replace_revision is None:
                    raise RecipeExistsError(existing)
                if existing["revision"] != replace_revision:
                    raise ConflictError(
                        "This recipe changed before replacement. Import it again to review the warning."
                    )
                identity = existing["id"]
            elif replace_revision not in (None, 0):
                raise ConflictError("This recipe changed before replacement. Import it again to review the warning.")
        history = [(path, row) for path, row in self._runs() if row["recipeId"] == identity]
        if not existing and history and replace_revision is None and not initializing:
            raise RecipeExistsError({"id": identity, "revision": 0})
        if replace_revision is not None and any(row["status"] in ACTIVE for _, row in history):
            raise ConflictError("Stop this recipe's active decisions before replacing it.")
        now = time.time()
        metadata = {
            **index["recipes"].get(identity, {}),
            "revision": existing["revision"] + 1 if existing else 1,
            "createdAt": existing["createdAt"] if existing else now,
            "updatedAt": now,
            "hash": document_hash(document),
        }
        if not revision:
            for key in ("publication", "pendingPublication", "importSource"):
                metadata.pop(key, None)
        if template_id:
            metadata["templateId"] = template_id
        filename = existing["filename"] if existing else filename
        if dry_run:
            return {
                "identity": identity,
                "filename": filename,
                "document": document,
                "metadata": metadata,
                "historyPaths": [str(path.relative_to(self.history_dir)) for path, _ in history]
                if replace_revision is not None
                else [],
                "oldMetadata": index["recipes"].get(identity, {}),
            }
        write_json(self.recipe_dir / filename, document)
        if not revision:
            self._clear_sharing_sidecars(index["recipes"].get(identity, {}))
        if replace_revision is not None:
            for path, _ in history:
                path.unlink()
        index["recipes"][identity] = metadata
        return {
            "id": identity,
            "filename": filename,
            "document": document,
            **{k: v for k, v in metadata.items() if k not in ("hash", "pendingPublication")},
        }

    def save(self, document, identity=None, revision=None, replace_revision=None, filename=None):
        with self.transaction() as index:
            return self._save(index, document, identity, revision, replace_revision=replace_revision, filename=filename)

    def _import(self, index, template_id, document, replace_revision=None):
        return self._save(
            index, document, template_id=template_id, filename=template_id + ".json", replace_revision=replace_revision
        ), replace_revision is None

    def import_template(self, template_id, document, replace_revision=None):
        with self.transaction() as index:
            return self._import(index, template_id, document, replace_revision)

    def _apply_shared_import(self, index, journal):
        identity = recipe_reference(journal["identity"])
        filename = recipe_filename(journal["filename"])
        if filename[:-5] != identity:
            raise StorageError("The shared import journal has an invalid filename.")
        history_paths = []
        for relative in journal["historyPaths"]:
            path = Path(relative)
            if path.is_absolute() or len(path.parts) != 2 or path.parts[0] != identity:
                raise StorageError("The shared import journal has an invalid history path.")
            identity_path(path.stem)
            if path.suffix not in (".md", ".json"):
                raise StorageError("The shared import journal has an invalid history record.")
            history_paths.append(self.history_dir / path)
        write_json(self.recipe_dir / filename, journal["document"])
        for path in history_paths:
            path.unlink(missing_ok=True)
        self._clear_sharing_sidecars(journal["oldMetadata"])
        index["recipes"][identity] = journal["metadata"]

    def import_shared(self, document, filename, replace_revision, source, execution):
        # The journal makes the recipe, source attribution and confirmed history clearing
        # recover together after a process interruption, while retaining sequence counters.
        with self.transaction() as index:
            journal = self._save(index, document, filename=filename, replace_revision=replace_revision, dry_run=True)
            journal["metadata"]["importSource"] = source
            write_json(
                self.root / "source-examples" / source["exampleRef"], {"document": document, "execution": execution}
            )
            write_json(self.root / ".shared-import.json", journal)
            self._apply_shared_import(index, journal)
            return {
                "id": journal["identity"],
                "filename": journal["filename"],
                "document": document,
                **{k: v for k, v in journal["metadata"].items() if k not in ("hash", "pendingPublication")},
                "sourceExample": execution,
                "sourceDocument": document,
            }

    def _clear_sharing_sidecars(self, metadata):
        source = metadata.get("importSource", {}).get("exampleRef", "")
        if re.fullmatch(r"[a-f0-9]{32}\.json", source):
            (self.root / "source-examples" / source).unlink(missing_ok=True)
        pending = metadata.get("pendingPublication", {}).get("idempotencyKey", "")
        if re.fullmatch(r"[a-f0-9]{32}", pending):
            (self.root / "pending" / (pending + ".json")).unlink(missing_ok=True)

    def delete_recipe(self, identity):
        recipe_reference(identity)
        with self.transaction() as index:
            row = next((row for row in self._recipes(index) if row["id"] == identity), None)
            if row:
                (self.recipe_dir / row["filename"]).unlink(missing_ok=True)
            self._clear_sharing_sidecars(index["recipes"].get(identity, {}))
            index["recipes"].pop(identity, None)
            index["favourites"] = [key for key in index["favourites"] if key != identity]
            return int(row is not None)

    def favourites(self):
        with self.transaction() as index:
            self._recipes(index)
            return list(index["favourites"])

    def favourite(self, identity, enabled):
        with self.transaction() as index:
            if not any(row["id"] == identity for row in self._recipes(index)):
                raise ValueError("Recipe not found.")
            index["favourites"] = [key for key in index["favourites"] if key != identity]
            if enabled:
                index["favourites"].append(identity)

    def _runs(self):
        rows = []
        for path in self.history_dir.glob("*/*"):
            if not path.is_file() or path.suffix.lower() not in (".json", ".md"):
                continue
            row = read_history(path)
            if row["status"] in ACTIVE and row["leaseUntil"] < time.time():
                row.update(
                    status="interrupted",
                    completedAt=time.time(),
                    error="The server stopped before this request completed. It has not been automatically retried.",
                )
                write_history(path, row)
            rows.append((path, row))
        return rows

    def submit(self, submission, recipe, inputs, request, owner, recipe_id=None, revision=None):
        fingerprint = hashlib.sha256(encode({"recipe": recipe, "input": inputs}).encode()).hexdigest()
        with self.transaction() as index:
            rows = self._runs()
            old = next((row for _, row in rows if row["submissionId"] == submission), None)
            if old:
                if old["requestHash"] != fingerprint:
                    raise ConflictError("This submission ID already belongs to different input.")
                return self.run_row(old), False
            if recipe_id:
                source = next((row for row in self._recipes(index) if row["id"] == recipe_id), None)
                if not source or source["revision"] != revision:
                    raise ConflictError("This recipe changed before submission. Reload it before running again.")
            if sum(row["status"] in ACTIVE for _, row in rows) >= 2:
                raise BusyError("Two decisions are already running. Wait for one to finish.")
            group = recipe_reference(recipe_id) if recipe_id else "_drafts"
            counters = index.setdefault("historySequences", {})
            prefix = group + "-"
            last = max(
                [counters.get(group, 0)]
                + [
                    int(row["id"][len(prefix) :])
                    for _, row in rows
                    if row["id"].startswith(prefix) and re.fullmatch(r"[0-9]{5,}", row["id"][len(prefix) :])
                ]
            )
            identity, now = identity_path(f"{group}-{last + 1:05d}"), time.time()
            counters[group] = last + 1
            # Reserve under the lock before writing the record; deleted/failed writes
            # must not let an old browser reference resolve to a different decision.
            write_json(self.index_path, index)
            row = {
                "id": identity,
                "submissionId": submission,
                "requestHash": fingerprint,
                "recipeId": recipe_id,
                "recipeRevision": revision,
                "recipe": recipe,
                "input": inputs,
                "request": request,
                "status": "pending",
                "owner": owner,
                "leaseUntil": now + 90,
                "createdAt": now,
                "completedAt": None,
                "durationMs": None,
                "response": None,
                "answers": None,
                "error": None,
            }
            write_history(self.history_dir / group / (identity + ".md"), row)
            return self.run_row(row), True

    @staticmethod
    def run_row(row, summary=False):
        if row is None:
            return None
        result = json.loads(encode(row))
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        result.update(
            name=result["recipe"]["name"],
            model=response.get("model") or result["request"]["model"],
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else None,
        )
        for name in ("owner", "leaseUntil", "requestHash", "submissionId"):
            result.pop(name)
        if summary:
            for name in ("recipe", "input", "request", "response", "answers"):
                result.pop(name)
        return result

    def run(self, identity):
        identity_path(identity)
        with self.transaction():
            return self.run_row(next((row for _, row in self._runs() if row["id"] == identity), None))

    def history(self, recipe_id=None, cursor=None, limit=20):
        if recipe_id:
            recipe_reference(recipe_id)
        boundary = None
        if cursor:
            try:
                timestamp, identity = cursor.split(":", 1)
                boundary = (float(timestamp), identity_path(identity))
                if not math.isfinite(boundary[0]):
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError("Invalid history cursor.") from None
        with self.transaction():
            rows = [
                row
                for _, row in self._runs()
                if (not recipe_id or row["recipeId"] == recipe_id)
                and (not boundary or (row["createdAt"], row["id"]) < boundary)
            ]
        rows.sort(key=lambda row: (row["createdAt"], row["id"]), reverse=True)
        page = rows[:limit]
        return {
            "items": [self.run_row(row, True) for row in page],
            "cursor": f"{page[-1]['createdAt']}:{page[-1]['id']}" if len(rows) > limit else None,
        }

    def start(self, identity, owner):
        identity_path(identity)
        with self.transaction(wait=True):
            for path, row in self._runs():
                if row["id"] == identity and row["owner"] == owner and row["status"] == "pending":
                    row["status"] = "running"
                    write_history(path, row)
                    return 1
            return 0

    def finish(self, identity, status, response=None, answers=None, error=None, owner=None):
        identity_path(identity)
        with self.transaction(wait=True):
            for path, row in self._runs():
                if row["id"] == identity and row["status"] in ACTIVE and (not owner or row["owner"] == owner):
                    now = time.time()
                    row.update(
                        status=status,
                        response=response,
                        answers=answers,
                        error=error,
                        completedAt=now,
                        durationMs=int((now - row["createdAt"]) * 1000),
                    )
                    write_history(path, row)
                    return 1
            return 0

    def delete_run(self, identity=None):
        if identity:
            identity_path(identity)
        with self.transaction():
            count = 0
            for path, row in self._runs():
                if identity and row["id"] != identity:
                    continue
                if row["status"] in ACTIVE:
                    if identity:
                        raise ConflictError("Stop this decision before deleting it.")
                    continue
                path.unlink()
                count += 1
            return count

    def migrate(self, legacy_path, index, templates):
        if not legacy_path.exists():
            return
        import sqlite3

        with sqlite3.connect(legacy_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "jev_recipe" not in tables:
                return
            mapping = {}
            for row in db.execute("SELECT * FROM jev_recipe"):
                document = json.loads(row["document"])
                filename = default_filename(document["name"])
                identity = filename[:-5]
                base, suffix = identity, 2
                while identity.casefold() in {key.casefold() for key in index["recipes"]}:
                    identity = recipe_reference(base[:100] + f" ({suffix})")
                    filename = identity + ".json"
                    suffix += 1
                mapping[row["id"]] = identity
                path = self.recipe_dir / filename
                if not path.exists():
                    write_json(path, document)
                    index["recipes"][identity] = {
                        "revision": row["revision"],
                        "createdAt": row["createdAt"],
                        "updatedAt": row["updatedAt"],
                        "hash": document_hash(document),
                    }
            legacy_runs = list(db.execute("SELECT * FROM jev_run")) if "jev_run" in tables else []
            favourites = (
                [row[0] for row in db.execute("SELECT id FROM jev_favourite")] if "jev_favourite" in tables else []
            )
            for row in legacy_runs:
                old = row["recipeId"]
                if old and old.startswith("builtin:") and old not in mapping:
                    # Past runs retain their full recipe snapshot, without populating
                    # the library with templates the user has not chosen to import.
                    mapping[old] = re.sub(r"^\d+-", "", old.removeprefix("builtin:"))
            for old in favourites:
                template_id = re.sub(r"^\d+-", "", old.removeprefix("builtin:"))
                if old.startswith("builtin:") and template_id in templates:
                    document = templates[template_id]["document"]
                    identity = template_id
                    if identity not in index["recipes"]:
                        self._import(index, template_id, document)
                    mapping[old] = identity
                identity = mapping.get(old, old)
                if identity in index["recipes"] and identity not in index["favourites"]:
                    index["favourites"].append(identity)
            for original in legacy_runs:
                row = dict(original)
                for name in ("recipe", "input", "request", "response", "answers"):
                    row[name] = json.loads(row[name]) if row[name] else None
                row["recipeId"] = mapping.get(
                    row["recipeId"], default_filename(row["recipe"]["name"])[:-5] if row["recipeId"] else None
                )
                group = recipe_reference(row["recipeId"]) if row["recipeId"] else "_drafts"
                path = self.history_dir / group / (identity_path(row["id"]) + ".json")
                if not path.exists():
                    write_history(path, row)
