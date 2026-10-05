import asyncio
import copy
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.extensions.jev import bundled_recipes, install
from llms.extensions.jev.client import MAX_RESPONSE, DecisionClient, DecisionError, normalize_answers, provider_status
from llms.extensions.jev.execution import Executor
from llms.extensions.jev.generation import generate
from llms.extensions.jev.schema import ValidationError, compile_request, validate_recipe
from llms.extensions.jev.storage import (
    BusyError,
    ConflictError,
    JevStore,
    RecipeExistsError,
    read_history,
    read_json,
    write_history,
    write_json,
)


def starter():
    return copy.deepcopy(bundled_recipes()["support"]["document"])


def sample_response(recipe):
    answers = {}
    for name, q in recipe["questions"].items():
        if q["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.96}
        elif q["type"] == "choice":
            options = list(q["criteria"])
            answers[name] = {
                "type": "choice",
                "choice": options[0],
                "confidence": 0.67,
                "probabilities": {v: 0.78 if i == 0 else 0.22 if i == 1 else 0 for i, v in enumerate(options)},
            }
        else:
            answers[name] = {
                "type": "score",
                "score": 1.99,
                "confidence": 0.99,
                "probabilities": {str(i): 1 if i == 2 else 0 for i in range(len(q["criteria"]))},
                "legend": {str(i): v for i, v in enumerate(q["criteria"])},
            }
    return {"model": "typesafe/jev-1.13-20260917", "answers": answers, "usage": {"cost": 0.000019992}}


class RecipeTests(TestCase):
    def test_all_starters_and_contrasting_examples_compile(self):
        self.assertEqual(len(bundled_recipes()), 7)
        for row in bundled_recipes().values():
            doc = validate_recipe(row["document"])
            self.assertGreaterEqual(len(doc["examples"]), 2)
            for case in doc["examples"]:
                request = compile_request(doc, case["input"])
                self.assertEqual(request["state"], case["input"])
                self.assertNotIn("presentation", request)

    def test_compilation_preserves_input_and_recipe(self):
        doc = starter()
        inputs = {"customer_tier": "Standard", "ticket": 'Quote: "it\'s"\nUnicode: ☃'}
        request = compile_request(doc, inputs)
        request["state"]["ticket"] = "changed"
        request["questions"]["urgency"]["criteria"].reverse()
        self.assertIn("☃", inputs["ticket"])
        self.assertEqual(doc["questions"]["urgency"]["criteria"][0], "Can wait for a future release")

    def test_schema_rejects_remote_refs_unsupported_features_and_bad_defaults(self):
        for change in (
            {"$ref": "https://example.com/schema"},
            {"oneOf": []},
            {"minimum": "small"},
            {"enum": ["A", "A"]},
            {"enum": ["A"], "default": "B"},
        ):
            doc = starter()
            doc["inputSchema"]["properties"]["ticket"].update(change)
            with self.assertRaises(ValidationError):
                validate_recipe(doc)

    def test_invalid_types_empty_required_unknown_inputs_and_nonfinite_values(self):
        doc = starter()
        inputs = doc["examples"][0]["input"]
        for changed in (
            {**inputs, "ticket": "   "},
            {**inputs, "ticket": 42},
            {**inputs, "secret": "extra"},
            {**inputs, "ticket": float("nan")},
            {"ticket": "hello"},
        ):
            with self.assertRaises(ValidationError):
                compile_request(doc, changed)

    def test_question_limits_labels_future_version_and_structured_criteria(self):
        for mutation in (
            lambda d: d.update(schemaVersion=2),
            lambda d: d["questions"]["urgency"].update(criteria=["only one"]),
            lambda d: d["questions"]["team"].update(instructions={"nested": "unsupported"}),
            lambda d: d["presentation"]["questions"].update(missing={"label": "Gone"}),
            lambda d: d.update(user="other"),
        ):
            doc = starter()
            mutation(doc)
            with self.assertRaises(ValidationError):
                validate_recipe(doc)

    def test_binary_false_and_numeric_zero_are_valid_required_inputs(self):
        doc = starter()
        doc["inputSchema"] = {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "count": {"type": "integer"}},
            "required": ["enabled", "count"],
        }
        doc["examples"] = []
        self.assertEqual(compile_request(doc, {"enabled": False, "count": 0})["state"]["count"], 0)

    def test_text_mapping_validates_field_and_compiles_literal_text(self):
        doc = starter()
        doc["state"] = {"mode": "text", "field": "ticket"}
        self.assertEqual(
            compile_request(doc, doc["examples"][0]["input"])["state"], doc["examples"][0]["input"]["ticket"]
        )
        doc["state"]["field"] = "absent"
        with self.assertRaises(ValidationError):
            validate_recipe(doc)


class ResponseTests(TestCase):
    def test_all_answer_types_and_missing_confidence_remain_distinct(self):
        doc = starter()
        response = sample_response(doc)
        response["answers"]["team"].pop("confidence")
        answers = normalize_answers(response, doc["questions"])
        self.assertEqual(answers["is_bug"], {"type": "noul", "noul": 0.96})
        self.assertIsNone(answers["team"]["confidence"])
        self.assertEqual(answers["urgency"]["score"], 1.99)

    def test_malformed_results_are_never_fabricated(self):
        doc = starter()
        for mutation in (
            lambda r: r["answers"].pop("is_bug"),
            lambda r: r["answers"]["team"].update(choice="missing"),
            lambda r: r["answers"]["is_bug"].update(noul=True),
            lambda r: r["answers"]["urgency"].update(score=3),
            lambda r: r["answers"]["team"].update(probabilities={"wrong": 1}),
            lambda r: r["answers"]["team"].update(confidence=float("inf")),
            lambda r: r["answers"]["urgency"]["legend"].update({"1": "wrong scale"}),
        ):
            response = sample_response(doc)
            mutation(response)
            with self.assertRaises(ValidationError):
                normalize_answers(response, doc["questions"])

    def test_provider_status_does_not_expose_credentials(self):
        ctx = SimpleNamespace(get_registered_provider=lambda _: None)
        self.assertIsNone(provider_status(ctx)[0])
        ctx.get_registered_provider = lambda _: SimpleNamespace(api_key="secret")
        self.assertNotIn("secret", provider_status(ctx)[1])


class StorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = JevStore(Path(self.temp.name) / "one")

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, submission="submission", document=None):
        doc = document or starter()
        inputs = doc["examples"][0]["input"]
        return self.db.submit(submission, doc, inputs, compile_request(doc, inputs), "owner")

    def test_personal_recipes_revisions_and_user_isolation(self):
        row = self.db.save(starter())
        document = copy.deepcopy(row["document"])
        document["name"] = "My recipe"
        saved = self.db.save(document, row["id"], row["revision"])
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["id"], row["id"])
        self.assertEqual(self.db.recipe(row["id"])["document"]["name"], "My recipe")
        with self.assertRaises(ConflictError):
            self.db.save(document, row["id"], 1)
        other = JevStore(Path(self.temp.name) / "two")
        self.assertIsNone(other.recipe(row["id"]))
        self.db.favourite(saved["id"], True)
        self.assertIn(saved["id"], self.db.favourites())
        self.db.delete_recipe(saved["id"])
        self.assertNotIn(saved["id"], self.db.favourites())

    def test_submission_idempotency_conflicts_and_active_limit(self):
        first, created = self.submit()
        self.assertTrue(created)
        again, created = self.submit()
        self.assertFalse(created)
        self.assertEqual(first["id"], again["id"])
        changed = starter()
        changed["name"] = "Changed"
        with self.assertRaises(ConflictError):
            self.submit(document=changed)
        self.submit("second")
        with self.assertRaises(BusyError):
            self.submit("third")

    def test_readable_markdown_history_sequences_survive_deletions_and_restarts(self):
        doc = starter()
        source = self.db.save(doc, filename="sentiment.json")
        inputs = doc["examples"][0]["input"]
        request = compile_request(doc, inputs)

        def submit(key, store=self.db):
            return store.submit(key, doc, inputs, request, "owner", source["id"], 1)[0]

        first = submit("one")
        self.assertEqual(first["id"], "sentiment-00001")
        path = self.db.history_dir / "sentiment" / "sentiment-00001.md"
        self.assertTrue(path.read_text().startswith("# sentiment-00001\n"))
        self.assertIn("## Input", path.read_text())
        self.assertEqual(read_history(path)["recipe"], doc)
        self.db.start(first["id"], "owner")
        response = sample_response(doc)
        answers = normalize_answers(response, doc["questions"])
        self.db.finish(first["id"], "succeeded", response, answers)
        self.assertEqual(read_history(path)["answers"], answers)
        self.assertIn("- Status: succeeded", path.read_text())
        self.assertEqual(submit("one")["id"], first["id"])
        second = submit("two")
        self.assertEqual(second["id"], "sentiment-00002")
        self.db.finish(second["id"], "cancelled")
        self.db.delete_run(second["id"])
        reopened = JevStore(Path(self.temp.name) / "one")
        third = submit("three", reopened)
        self.assertEqual(third["id"], "sentiment-00003")
        reopened.finish(third["id"], "cancelled")
        reopened.delete_run()
        fourth = submit("four", reopened)
        self.assertEqual(fourth["id"], "sentiment-00004")
        draft, _ = self.submit("draft")
        self.assertEqual(draft["id"], "_drafts-00001")

    def test_history_sequence_recovers_from_files_and_failed_record_creation(self):
        doc = starter()
        source = self.db.save(doc, filename="Révision 日本語 & triage.json")
        inputs = doc["examples"][0]["input"]
        request = compile_request(doc, inputs)
        first, _ = self.db.submit("one", doc, inputs, request, "owner", source["id"], 1)
        self.db.finish(first["id"], "succeeded")
        path = self.db.history_dir / source["id"] / (first["id"] + ".md")
        raw = read_history(path)
        raw["id"] = source["id"] + "-00007"
        write_history(path.with_name(raw["id"] + ".md"), raw)
        path.unlink()
        index = read_json(self.db.index_path)
        index.pop("historySequences")
        write_json(self.db.index_path, index)
        with (
            patch("llms.extensions.jev.storage.write_history", side_effect=OSError("record failure")),
            self.assertRaises(OSError),
        ):
            self.db.submit("two", doc, inputs, request, "owner", source["id"], 1)
        next_run, _ = self.db.submit("two", doc, inputs, request, "owner", source["id"], 1)
        self.assertEqual(next_run["id"], source["id"] + "-00009")
        self.assertEqual(self.db.run(next_run["id"])["input"], inputs)
        page = self.db.history(limit=1)
        self.assertEqual(self.db.history(cursor=page["cursor"])["items"][0]["id"], raw["id"])

    def test_concurrent_stores_allocate_unique_history_numbers(self):
        from concurrent.futures import ThreadPoolExecutor

        stores = [self.db, JevStore(Path(self.temp.name) / "one")]
        source = self.db.save(starter(), filename="sentiment.json")
        doc = source["document"]
        inputs = doc["examples"][0]["input"]
        request = compile_request(doc, inputs)

        def submit_many(worker):
            ids = []
            for number in range(3):
                for attempt in range(200):
                    try:
                        run, _ = stores[worker].submit(
                            f"{worker}-{number}", doc, inputs, request, "owner", source["id"], 1
                        )
                        break
                    except ConflictError:
                        if attempt == 199:
                            raise
                        time.sleep(0.001)
                stores[worker].finish(run["id"], "succeeded")
                ids.append(run["id"])
            return ids

        with ThreadPoolExecutor(max_workers=2) as workers:
            ids = [identity for batch in workers.map(submit_many, range(2)) for identity in batch]
        self.assertEqual(sorted(ids), [f"sentiment-{number:05d}" for number in range(1, 7)])
        self.assertEqual(len(self.db.history()["items"]), 6)

    def test_legacy_json_history_and_atomic_markdown_updates(self):
        run, _ = self.submit()
        path = self.db.history_dir / "_drafts" / (run["id"] + ".md")
        raw = read_history(path)
        old_id = "aec1e4f1-aa47-4011-93d7-7191f4c31c32"
        raw["id"] = old_id
        legacy = path.with_name(old_id + ".json")
        write_json(legacy, raw)
        path.unlink()
        self.assertEqual(self.db.run(old_id)["input"], raw["input"])
        self.db.finish(old_id, "cancelled")
        self.assertEqual(read_json(legacy)["status"], "cancelled")
        new, _ = self.submit("new")
        new_path = self.db.history_dir / "_drafts" / (new["id"] + ".md")
        before = new_path.read_bytes()
        with (
            patch("llms.extensions.jev.storage.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            self.db.finish(new["id"], "succeeded")
        self.assertEqual(new_path.read_bytes(), before)
        self.assertEqual(self.db.run(new["id"])["status"], "pending")
        self.assertEqual(list(new_path.parent.glob(".jev-*")), [])
        new_path.write_text("# Invalid record\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.db.run(new["id"])

    def test_cancellation_wins_late_completion_and_expired_runs_recover(self):
        run, _ = self.submit()
        self.db.start(run["id"], "owner")
        self.db.finish(run["id"], "cancelled")
        self.assertFalse(self.db.finish(run["id"], "succeeded", sample_response(starter())))
        self.assertEqual(self.db.run(run["id"])["status"], "cancelled")
        pending, _ = self.submit("second")
        path = next(self.db.history_dir.glob("*/" + pending["id"] + ".md"))
        row = read_history(path)
        row["leaseUntil"] = time.time() - 1
        write_history(path, row)
        self.assertEqual(self.db.run(pending["id"])["status"], "interrupted")

    def test_history_snapshots_pagination_delete_and_hidden_metadata(self):
        source = self.db.save(starter())
        for i in range(3):
            run, _ = self.submit(str(i))
            self.db.finish(
                run["id"],
                "succeeded",
                sample_response(starter()),
                normalize_answers(sample_response(starter()), starter()["questions"]),
            )
        first = self.db.history(limit=2)
        second = self.db.history(cursor=first["cursor"], limit=2)
        self.assertEqual(len(second["items"]), 1)
        self.assertNotIn("input", first["items"][0])
        detail = self.db.run(first["items"][0]["id"])
        self.assertNotIn("owner", detail)
        self.db.delete_recipe(source["id"])
        self.assertEqual(detail["recipe"]["name"], "Support triage")
        self.assertEqual(self.db.delete_run(), 3)

    def test_default_is_one_editable_portable_json_copy_and_stays_deleted(self):
        catalog = bundled_recipes()
        store = JevStore(Path(self.temp.name) / "fresh", catalog)
        (row,) = store.recipes()
        self.assertEqual(row["document"]["name"], "Message sentiment")
        self.assertNotIn("subject", row["document"]["inputSchema"]["properties"])
        self.assertEqual(row["document"]["inputSchema"]["properties"]["message"]["title"], "Email, tweet or comment")
        self.assertEqual(read_json(store.recipe_dir / row["filename"]), row["document"])
        self.assertFalse((Path(self.temp.name) / "fresh" / "jev.sqlite").exists())
        store.delete_recipe(row["id"])
        self.assertEqual(JevStore(Path(self.temp.name) / "fresh", catalog).recipes(), [])

    def test_first_account_setup_preserves_existing_default_history(self):
        catalog = bundled_recipes()
        root = Path(self.temp.name) / "history-before-initialization"
        store = JevStore(root)
        doc = catalog["sentiment"]["document"]
        saved = store.save(doc, filename="sentiment.json")
        inputs = doc["examples"][0]["input"]
        run, _ = store.submit("initial-history", doc, inputs, compile_request(doc, inputs), "owner", saved["id"], 1)
        store.finish(run["id"], "succeeded")
        store.delete_recipe(saved["id"])
        index = read_json(store.index_path)
        index.pop("initialized")
        write_json(store.index_path, index)
        initialized = JevStore(root, catalog)
        self.assertEqual([row["id"] for row in initialized.recipes()], ["sentiment"])
        self.assertEqual(initialized.run(run["id"])["recipe"], doc)
        self.assertTrue(read_json(initialized.index_path)["initialized"])
        self.assertEqual(len(JevStore(root, catalog).history()["items"]), 1)
        # Only an explicit import asks to replace the default and clear its history.
        with self.assertRaises(RecipeExistsError):
            initialized.import_template("sentiment", doc)

    def test_initial_setup_preserves_existing_default_with_different_case(self):
        catalog = bundled_recipes()
        root = Path(self.temp.name) / "existing-default"
        store = JevStore(root)
        doc = {**catalog["sentiment"]["document"], "name": "message sentiment", "description": "My edits"}
        saved = store.save(doc, filename="SENTIMENT.json")
        store.favourite(saved["id"], True)
        index = read_json(store.index_path)
        index.pop("initialized")
        write_json(store.index_path, index)
        initialized = JevStore(root, catalog)
        self.assertEqual(initialized.recipe(saved["id"])["document"], doc)
        self.assertEqual(initialized.favourites(), [saved["id"]])
        self.assertEqual(len(initialized.recipes()), 1)

    def test_first_account_setup_recovers_after_initial_index_write_failure(self):
        catalog = bundled_recipes()
        root = Path(self.temp.name) / "interrupted-setup"

        def interrupt(path, value):
            if path.name == "index.json":
                raise OSError("initial metadata failure")
            return write_json(path, value)

        with patch("llms.extensions.jev.storage.write_json", side_effect=interrupt), self.assertRaises(OSError):
            JevStore(root, catalog)
        initialized = JevStore(root, catalog)
        self.assertEqual([row["id"] for row in initialized.recipes()], ["sentiment"])
        self.assertEqual(initialized.recipes()[0]["revision"], 1)
        self.assertTrue(read_json(initialized.index_path)["initialized"])

    def test_import_same_filename_requires_confirmation_and_clears_only_its_history(self):
        doc = starter()
        row, created = self.db.import_template("support", doc)
        self.assertTrue(created)
        document = copy.deepcopy(doc)
        document["description"] = "My triage"
        saved = self.db.save(document, row["id"], row["revision"])
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit(
            "saved", doc, inputs, compile_request(doc, inputs), "owner", saved["id"], saved["revision"]
        )
        self.db.finish(run["id"], "succeeded")
        unrelated, _ = self.submit("unrelated")
        self.db.finish(unrelated["id"], "succeeded")
        with self.assertRaises(RecipeExistsError) as warning:
            self.db.import_template("support", doc)
        self.assertEqual(warning.exception.recipe, {"id": "support", "revision": 2})
        self.assertEqual(self.db.recipe(row["id"])["document"], document)
        self.assertIsNotNone(self.db.run(run["id"]))
        imported, created = self.db.import_template("support", doc, 2)
        self.assertFalse(created)
        self.assertEqual(imported["id"], row["id"])
        self.assertEqual(imported["document"], doc)
        self.assertIsNone(self.db.run(run["id"]))
        self.assertIsNotNone(self.db.run(unrelated["id"]))
        self.assertEqual(len(self.db.recipes()), 1)

    def test_on_disk_edits_conflict_and_atomic_failure_preserves_previous_document(self):
        row = self.db.save(starter())
        path = self.db.recipe_dir / row["filename"]
        changed = copy.deepcopy(row["document"])
        changed["description"] = "Edited on disk"
        write_json(path, changed)
        with self.assertRaises(ConflictError):
            self.db.save(row["document"], row["id"], row["revision"])
        current = self.db.recipe(row["id"])
        self.assertEqual(current["revision"], 2)
        with (
            patch("llms.extensions.jev.storage.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            self.db.save(row["document"], row["id"], current["revision"])
        self.assertEqual(read_json(path), changed)
        self.assertEqual(list(self.db.recipe_dir.glob(".jev-*")), [])

    def test_import_recovers_without_duplicates_after_index_write_failure(self):
        def interrupt(path, value):
            if path == self.db.index_path:
                raise OSError("metadata failure")
            return write_json(path, value)

        with patch("llms.extensions.jev.storage.write_json", side_effect=interrupt), self.assertRaises(OSError):
            self.db.import_template("support", starter())
        with self.assertRaises(RecipeExistsError):
            self.db.import_template("support", starter())
        row, created = self.db.import_template("support", starter(), 1)
        self.assertFalse(created)
        self.assertEqual(row["templateId"], "support")
        self.assertEqual(len(self.db.recipes()), 1)

    def test_metadata_recovers_after_interrupted_recipe_write(self):
        row = self.db.save(starter())
        document = copy.deepcopy(row["document"])
        document["name"] = "Saved before metadata interruption"

        def interrupt(path, value):
            if path == self.db.index_path:
                raise OSError("metadata failure")
            return write_json(path, value)

        with patch("llms.extensions.jev.storage.write_json", side_effect=interrupt), self.assertRaises(OSError):
            self.db.save(document, row["id"], row["revision"])
        recovered = self.db.recipe(row["id"])
        self.assertEqual(recovered["revision"], 2)
        self.assertEqual(recovered["document"], document)

    def test_store_instances_share_lock_and_reject_path_traversal(self):
        other = JevStore(Path(self.temp.name) / "one")
        with self.db.transaction(), self.assertRaises(ConflictError):
            other.save(starter())
        for identity in ("../other", "../../secret", "C:\\file", "builtin:01-sentiment"):
            with self.assertRaises(ValueError):
                self.db.recipe(identity)
            with self.assertRaises(ValueError):
                self.db.delete_recipe(identity)
        self.assertEqual(other.recipes(), [])

    def test_filenames_are_unique_ids_and_names_are_independent(self):
        document = {**starter(), "name": "Shared display name: inputs / outputs"}
        row = self.db.save(document, filename="Révision 日本語 & triage.json")
        other = self.db.save(document, filename="other.json")
        self.assertEqual(row["id"], "Révision 日本語 & triage")
        self.assertEqual(other["document"], document)
        self.assertTrue((self.db.recipe_dir / row["filename"]).exists())
        with self.assertRaises(RecipeExistsError):
            self.db.save({**document, "name": "Different name"}, filename=row["filename"])
        with self.assertRaises(RecipeExistsError):
            self.db.save(document, filename="OTHER.JSON")
        for filename in (
            "../escape.json",
            "a/b.json",
            "a\\b.json",
            "NUL.json",
            "COM1.txt.json",
            "a:.json",
            ".json",
            "invalid.txt",
            " padded.json ",
        ):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.db.save(document, filename=filename)

    def test_changing_display_name_preserves_filename_stars_and_history(self):
        row = self.db.save(starter(), filename="triage.json")
        self.db.favourite(row["id"], True)
        doc = row["document"]
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("rename", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 1)
        changed = {**doc, "name": "Renamed recipe"}
        saved = self.db.save(changed, row["id"], 1)
        self.assertEqual(saved["id"], "triage")
        self.assertEqual(read_json(self.db.recipe_dir / "triage.json"), changed)
        self.assertTrue((self.db.history_dir / "triage" / (run["id"] + ".md")).exists())
        self.assertEqual(self.db.run(run["id"])["recipeId"], "triage")
        self.assertEqual(self.db.run(run["id"])["recipe"], doc)
        self.assertEqual(self.db.favourites(), ["triage"])
        self.assertEqual(len(self.db.history("triage")["items"]), 1)
        self.assertTrue(self.db.finish(run["id"], "succeeded"))

    def test_replacement_rejects_stale_confirmations_and_running_decisions(self):
        row = self.db.save(starter())
        changed = {**starter(), "description": "newer"}
        self.db.save(changed, row["id"], 1)
        with self.assertRaises(ConflictError):
            self.db.save(starter(), replace_revision=1)
        doc = starter()
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("active", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 2)
        with self.assertRaises(ConflictError):
            self.db.save(doc, replace_revision=2)
        self.assertIsNotNone(self.db.run(run["id"]))
        self.assertEqual(self.db.recipe(row["id"])["document"], changed)
        self.db.finish(run["id"], "cancelled")
        self.db.save(doc, replace_revision=2)
        self.assertIsNone(self.db.run(run["id"]))

    def test_deleted_recipe_history_requires_warning_before_recreation(self):
        row = self.db.save(starter())
        doc = row["document"]
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("deleted", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 1)
        self.db.finish(run["id"], "succeeded")
        self.db.delete_recipe(row["id"])
        with self.assertRaises(RecipeExistsError) as warning:
            self.db.save(doc)
        self.assertEqual(warning.exception.recipe["revision"], 0)
        self.db.save(doc, replace_revision=0)
        self.assertIsNone(self.db.run(run["id"]))

    def test_existing_files_keep_names_while_metadata_and_history_use_filename_stems(self):
        row = self.db.save(starter())
        self.db.favourite(row["id"], True)
        doc = row["document"]
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("migration", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 1)
        self.db.finish(run["id"], "succeeded")
        legacy_id = "aec1e4f1-aa47-4011-93d7-7191f4c31c32"
        index = read_json(self.db.index_path)
        index.pop("filenameStemIdentities")
        index["recipes"][legacy_id] = index["recipes"].pop(row["id"])
        index["favourites"] = [legacy_id]
        write_json(self.db.index_path, index)
        (self.db.recipe_dir / row["filename"]).rename(self.db.recipe_dir / (legacy_id + ".json"))
        history_path = self.db.history_dir / row["id"] / (run["id"] + ".md")
        raw = read_history(history_path)
        raw["recipeId"] = legacy_id
        write_history(self.db.history_dir / legacy_id / history_path.name, raw)
        history_path.unlink()
        migrated = JevStore(Path(self.temp.name) / "one")
        filename = legacy_id + ".json"
        self.assertEqual(migrated.recipe(legacy_id)["revision"], 1)
        self.assertEqual(migrated.favourites(), [legacy_id])
        self.assertEqual(migrated.run(run["id"])["recipeId"], legacy_id)
        self.assertEqual(migrated.run(run["id"])["recipe"], doc)
        self.assertTrue((migrated.recipe_dir / filename).exists())
        self.assertEqual(migrated.recipe(legacy_id)["previousIds"], [filename])
        self.assertEqual(JevStore(Path(self.temp.name) / "one").recipes(), migrated.recipes())

    def test_existing_files_with_duplicate_display_names_are_preserved(self):
        row = self.db.save(starter())
        duplicate = {**starter(), "description": "Separate file copy"}
        write_json(self.db.recipe_dir / "another.json", duplicate)
        index = read_json(self.db.index_path)
        index.pop("filenameStemIdentities")
        write_json(self.db.index_path, index)
        migrated = JevStore(Path(self.temp.name) / "one")
        self.assertEqual(migrated.recipe(row["id"])["document"], row["document"])
        self.assertEqual(migrated.recipe("another")["document"], duplicate)
        self.assertEqual(len(migrated.recipes()), 2)

    def test_full_filename_references_migrate_without_changing_files_or_snapshots(self):
        row = self.db.save(starter(), filename="portable.recipe.JSON")
        other = self.db.save(starter(), filename="portable.recipe.json.json")
        self.db.favourite(row["id"], True)
        doc = row["document"]
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("old-filename", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 1)
        self.db.finish(run["id"], "succeeded")
        dotted_run, _ = self.db.submit(
            "dotted-filename", doc, inputs, compile_request(doc, inputs), "owner", other["id"], 1
        )
        self.db.finish(dotted_run["id"], "succeeded")
        index = read_json(self.db.index_path)
        index.pop("filenameStemIdentities")
        index["filenameIdentities"] = True
        index["recipes"] = {item["filename"]: index["recipes"][item["id"]] for item in (row, other)}
        index["favourites"] = [row["filename"]]
        write_json(self.db.index_path, index)
        old_path = self.db.history_dir / row["id"] / (run["id"] + ".md")
        raw = read_history(old_path)
        raw["recipeId"] = row["filename"]
        write_history(self.db.history_dir / row["filename"] / old_path.name, raw)
        old_path.unlink()
        dotted_path = self.db.history_dir / other["id"] / (dotted_run["id"] + ".md")
        dotted_raw = read_history(dotted_path)
        dotted_raw["recipeId"] = other["filename"]
        write_history(self.db.history_dir / other["filename"] / dotted_path.name, dotted_raw)
        dotted_path.unlink()
        orphan = {**raw, "id": "orphan", "recipeId": "company-news.json"}
        write_history(self.db.history_dir / "company-news.json" / "orphan.json", orphan)
        before = (self.db.recipe_dir / row["filename"]).read_bytes()

        def interrupt(path, value):
            if path == self.db.index_path:
                raise OSError("metadata failure after reference migration")
            return write_json(path, value)

        with patch("llms.extensions.jev.storage.write_json", side_effect=interrupt), self.assertRaises(OSError):
            JevStore(Path(self.temp.name) / "one")
        migrated = JevStore(Path(self.temp.name) / "one")
        self.assertEqual(migrated.favourites(), ["portable.recipe"])
        self.assertEqual(migrated.recipe("portable.recipe")["filename"], "portable.recipe.JSON")
        self.assertEqual(migrated.recipe("portable.recipe.json")["revision"], 1)
        self.assertEqual(migrated.recipe("portable.recipe")["previousIds"], ["portable.recipe.JSON"])
        self.assertEqual(migrated.run(run["id"])["recipeId"], "portable.recipe")
        self.assertEqual(migrated.run(dotted_run["id"])["recipeId"], "portable.recipe.json")
        self.assertEqual(migrated.run(run["id"])["recipe"], doc)
        self.assertEqual(migrated.run("orphan")["recipeId"], "company-news")
        self.assertEqual((migrated.recipe_dir / row["filename"]).read_bytes(), before)
        self.assertFalse(migrated.reference_path.exists())
        migrated.save({**doc, "description": "Edit"}, row["id"], 1)
        self.assertFalse((migrated.recipe_dir / "portable.recipe.json").exists())
        self.assertEqual(len(migrated.recipes()), 2)
        self.assertTrue(migrated.delete_recipe(row["id"]))
        self.assertFalse((migrated.recipe_dir / row["filename"]).exists())

    def test_new_file_with_dotted_neighbor_gets_independent_metadata(self):
        old = self.db.save(starter(), filename="portable.json.json")
        self.db.save({**starter(), "description": "Existing edits"}, old["id"], 1)
        write_json(self.db.recipe_dir / "portable.json", starter())
        self.assertEqual(self.db.recipe("portable")["revision"], 1)
        self.assertEqual(self.db.recipe("portable.json")["revision"], 2)

    def test_on_disk_name_edit_preserves_filename_history_and_stars(self):
        row = self.db.save(starter(), filename="custom.json")
        self.db.favourite(row["id"], True)
        doc = row["document"]
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("external-name", doc, inputs, compile_request(doc, inputs), "owner", row["id"], 1)
        changed = {**doc, "name": "Edited on disk"}
        write_json(self.db.recipe_dir / row["filename"], changed)
        recovered = JevStore(Path(self.temp.name) / "one")
        self.assertEqual(recovered.recipe("custom")["revision"], 2)
        self.assertEqual(recovered.run(run["id"])["recipeId"], "custom")
        self.assertEqual(recovered.run(run["id"])["recipe"], doc)
        self.assertEqual(recovered.favourites(), ["custom"])

    def test_run_completion_waits_for_a_brief_write_lock(self):
        import threading

        run, _ = self.submit()
        outcomes, failures = [], []

        def finish():
            try:
                outcomes.append(self.db.finish(run["id"], "succeeded", sample_response(starter())))
            except Exception as error:
                failures.append(error)

        with self.db.transaction():
            thread = threading.Thread(target=finish)
            thread.start()
            time.sleep(0.03)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(outcomes, [1])
        self.assertEqual(self.db.run(run["id"])["status"], "succeeded")

    def test_sqlite_migration_preserves_recipes_stars_history_and_does_not_repeat(self):
        import sqlite3

        root = Path(self.temp.name) / "legacy"
        root.mkdir()
        legacy = root / "jev.sqlite"
        doc = starter()
        identity = "legacy-recipe"
        inputs = doc["examples"][0]["input"]
        run, _ = self.db.submit("legacy-submission", doc, inputs, compile_request(doc, inputs), "owner")
        raw_path = next(self.db.history_dir.glob("*/" + run["id"] + ".md"))
        raw = read_history(raw_path)
        raw["recipeId"] = identity
        raw["recipeRevision"] = 4
        raw["status"] = "succeeded"
        raw["response"] = sample_response(doc)
        raw["answers"] = normalize_answers(raw["response"], doc["questions"])
        with sqlite3.connect(legacy) as db:
            db.executescript("""
                CREATE TABLE jev_recipe(id TEXT, document TEXT, revision INTEGER, createdAt REAL, updatedAt REAL);
                CREATE TABLE jev_favourite(id TEXT);
                CREATE TABLE jev_run(id TEXT, submissionId TEXT, requestHash TEXT, recipeId TEXT,
                    recipeRevision INTEGER, recipe TEXT, input TEXT, request TEXT, status TEXT, owner TEXT,
                    leaseUntil REAL, createdAt REAL, completedAt REAL, durationMs INTEGER,
                    response TEXT, answers TEXT, error TEXT);
            """)
            db.execute("INSERT INTO jev_recipe VALUES(?,?,?,?,?)", (identity, json.dumps(doc), 4, 1, 2))
            db.execute("INSERT INTO jev_favourite VALUES(?)", (identity,))
            fields = [
                "id",
                "submissionId",
                "requestHash",
                "recipeId",
                "recipeRevision",
                "recipe",
                "input",
                "request",
                "status",
                "owner",
                "leaseUntil",
                "createdAt",
                "completedAt",
                "durationMs",
                "response",
                "answers",
                "error",
            ]
            values = [
                json.dumps(raw[key])
                if key in ("recipe", "input", "request", "response", "answers") and raw[key] is not None
                else raw[key]
                for key in fields
            ]
            db.execute("INSERT INTO jev_run VALUES(" + ",".join("?" for _ in fields) + ")", values)
            raw["id"] = "builtin-run"
            raw["recipeId"] = "builtin:02-support"
            raw["submissionId"] = "builtin-submission"
            values = [
                json.dumps(raw[key])
                if key in ("recipe", "input", "request", "response", "answers") and raw[key] is not None
                else raw[key]
                for key in fields
            ]
            db.execute("INSERT INTO jev_run VALUES(" + ",".join("?" for _ in fields) + ")", values)
            raw["id"] = "sentiment-run"
            raw["recipeId"] = "builtin:01-sentiment"
            raw["submissionId"] = "sentiment-submission"
            sentiment = bundled_recipes()["sentiment"]["document"]
            raw["recipe"] = sentiment
            raw["input"] = sentiment["examples"][0]["input"]
            raw["request"] = compile_request(sentiment, raw["input"])
            raw["response"] = sample_response(sentiment)
            raw["answers"] = normalize_answers(raw["response"], sentiment["questions"])
            values = [
                json.dumps(raw[key])
                if key in ("recipe", "input", "request", "response", "answers") and raw[key] is not None
                else raw[key]
                for key in fields
            ]
            db.execute("INSERT INTO jev_run VALUES(" + ",".join("?" for _ in fields) + ")", values)
        original = legacy.read_bytes()
        migrated = JevStore(root, bundled_recipes())
        self.assertEqual(migrated.recipe(doc["name"])["revision"], 4)
        self.assertIn(doc["name"], migrated.favourites())
        self.assertEqual(migrated.run(run["id"])["recipe"], doc)
        builtin_run = migrated.run("builtin-run")
        self.assertFalse(builtin_run["recipeId"].startswith("builtin:"))
        self.assertEqual(builtin_run["recipeId"], "support")
        self.assertIsNone(migrated.recipe("support"))
        self.assertEqual(len(migrated.recipes()), 2)  # Owned legacy recipe plus initial sentiment.
        self.assertEqual(migrated.run("sentiment-run")["recipeId"], "sentiment")
        self.assertEqual(migrated.run("sentiment-run")["recipe"], sentiment)
        with self.assertRaises(RecipeExistsError):
            migrated.import_template("support", doc)
        imported, created = migrated.import_template("support", doc, 0)
        self.assertFalse(created)
        self.assertEqual(imported["id"], builtin_run["recipeId"])
        self.assertEqual(len(migrated.history()["items"]), 2)
        migrated.delete_recipe(doc["name"])
        migrated.delete_run(run["id"])
        # Existing initialized JSON stores from earlier versions acquire the receipt too.
        migrated.migration_path.unlink()
        reopened = JevStore(root, bundled_recipes())
        self.assertIsNone(reopened.recipe(doc["name"]))
        self.assertIsNone(reopened.run(run["id"]))
        self.assertEqual(legacy.read_bytes(), original)
        # Resetting the whole JSON store must not resurrect the SQLite backup.
        import shutil

        shutil.rmtree(migrated.root)
        reset = JevStore(root, bundled_recipes())
        self.assertEqual([row["id"] for row in reset.recipes()], ["sentiment"])
        self.assertEqual(reset.history()["items"], [])
        self.assertEqual(list(reset.history_dir.glob("*")), [])
        self.assertEqual(legacy.read_bytes(), original)


class AsyncTests(IsolatedAsyncioTestCase):
    async def test_executor_dedup_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            db = JevStore(Path(directory))
            gate = asyncio.Event()

            async def wait_for_decision(_):
                await gate.wait()

            client = SimpleNamespace(decide=AsyncMock(side_effect=wait_for_decision), close=AsyncMock())
            executor = Executor(client)
            doc = starter()
            run, created = db.submit(
                "id",
                doc,
                doc["examples"][0]["input"],
                compile_request(doc, doc["examples"][0]["input"]),
                executor.owner,
            )
            executor.launch(db, run)
            await asyncio.sleep(0.01)
            result = executor.cancel(db, run["id"])
            self.assertEqual(result["status"], "cancelled")
            await executor.close()
            self.assertEqual(db.run(run["id"])["status"], "cancelled")
            self.assertEqual(client.decide.call_count, 1)

    async def test_client_http_failures_and_invalid_response_without_retry(self):
        recipe = starter()
        response = SimpleNamespace(status=402)
        session = Mock(closed=False)
        session.post.return_value.__aenter__ = AsyncMock(return_value=response)
        session.post.return_value.__aexit__ = AsyncMock(return_value=False)
        client = DecisionClient(
            SimpleNamespace(get_registered_provider=lambda _: SimpleNamespace(api_key="secret", headers={}))
        )
        client.session = session
        with self.assertRaisesRegex(DecisionError, "needs credits"):
            await client.decide(compile_request(recipe, recipe["examples"][0]["input"]))
        self.assertEqual(session.post.call_count, 1)
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])

    async def test_generation_cannot_invent_examples(self):
        doc = starter()
        doc["examples"][0]["provenance"] = "user-reviewed"
        doc["examples"][0]["execution"] = {"status": "succeeded", "answers": "fabricated output"}
        ctx = SimpleNamespace(
            get_providers=lambda: {},
            chat_completion=AsyncMock(
                return_value={"choices": [{"message": {"content": "```json\n" + json.dumps(doc) + "\n```"}}]}
            ),
        )
        result = await generate(ctx, "alice", {"goal": "Route tickets", "model": "chat-model"})
        self.assertEqual(result["recipe"]["examples"], [])
        self.assertIn("content", result["recipe"])
        self.assertEqual(
            ctx.chat_completion.call_args.kwargs["context"],
            {"tools": "none", "nohistory": True, "nostore": True, "user": "alice"},
        )
        too_many = copy.deepcopy(doc)
        too_many["tags"] = ["one", "two", "three", "four"]
        ctx.chat_completion.return_value = {"choices": [{"message": {"content": json.dumps(too_many)}}]}
        invalid_tags = await generate(ctx, "alice", {"goal": "Route tickets", "model": "chat-model"})
        self.assertIsNone(invalid_tags["recipe"])
        self.assertIn("3 tags", invalid_tags["diagnostic"])
        ctx.chat_completion.return_value = {"choices": [{"message": {"content": "not JSON"}}]}
        result = await generate(ctx, "alice", {"goal": "Route tickets", "model": "chat-model"})
        self.assertIsNone(result["recipe"])
        self.assertIn("diagnostic", result)

    async def test_improvement_preserves_saved_examples_and_ignores_generated_ones(self):
        original = starter()
        original["examples"][0]["provenance"] = "user-reviewed"
        request = compile_request(original, original["examples"][0]["input"])
        original["examples"][0]["execution"] = {
            "status": "succeeded",
            "input": copy.deepcopy(original["examples"][0]["input"]),
            "prompt": request["state"],
            "answers": normalize_answers(sample_response(original), original["questions"]),
            "model": "typesafe/jev-test",
            "completedAt": "2026-10-04T00:00:00Z",
        }
        snapshot = copy.deepcopy(original)
        proposal = copy.deepcopy(original)
        proposal["description"] = "A clearer description."
        proposal["examples"] = [{"input": "invented", "execution": "invented"}]
        ctx = SimpleNamespace(
            get_providers=lambda: {},
            chat_completion=AsyncMock(return_value={"choices": [{"message": {"content": json.dumps(proposal)}}]}),
        )
        body = {"goal": "Clarify the description", "model": "chat-model", "recipe": original}
        result = await generate(ctx, "alice", body, improve=True)
        self.assertEqual(result["recipe"]["examples"], original["examples"])
        self.assertEqual(result["recipe"]["description"], proposal["description"])
        self.assertEqual(original, snapshot)  # Original request remains unchanged.

        key = next(iter(proposal["questions"]))
        proposal["questions"][key]["instructions"] += " Focus on immediate requests."
        ctx.chat_completion.return_value = {"choices": [{"message": {"content": json.dumps(proposal)}}]}
        result = await generate(ctx, "alice", body, improve=True)
        self.assertEqual(len(result["recipe"]["examples"]), len(original["examples"]))
        for saved, previous in zip(result["recipe"]["examples"], original["examples"], strict=True):
            self.assertEqual(saved["input"], previous["input"])
            self.assertNotIn("execution", saved)
            self.assertNotIn("expected", saved)
        self.assertIn("execution", original["examples"][0])

        proposal["inputSchema"]["properties"]["new_required"] = {"type": "string"}
        proposal["inputSchema"]["required"].append("new_required")
        ctx.chat_completion.return_value = {"choices": [{"message": {"content": json.dumps(proposal)}}]}
        result = await generate(ctx, "alice", body, improve=True)
        self.assertEqual(result["recipe"]["examples"], [])

    async def test_authenticated_api_and_recipe_validation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            app, cleanups = web.Application(), []
            ctx = SimpleNamespace(
                assert_username=lambda request: request.headers.get("X-User", "default"),
                get_user_path=lambda user: str(Path(directory) / user),
                get_registered_provider=lambda _: None,
                register_cleanup_handler=cleanups.append,
            )
            for method in ("get", "post", "put", "delete"):
                setattr(
                    ctx,
                    "add_" + method,
                    lambda path, handler, method=method: app.router.add_route(
                        method.upper(), "/ext/jev/" + path, handler
                    ),
                )
            install(ctx)
            async with TestClient(TestServer(app)) as client:
                status = await (await client.get("/ext/jev/status")).json()
                self.assertFalse(status["available"])
                self.assertNotIn("api_key", status)
                initial = await (await client.get("/ext/jev/recipes", headers={"X-User": "alice"})).json()
                self.assertEqual(len(initial["items"]), 1)
                self.assertEqual(initial["items"][0]["name"], "Message sentiment")
                self.assertNotIn("builtin", initial["items"][0])
                self.assertEqual((await client.get("/ext/jev/templates", headers={"X-User": "alice"})).status, 404)
                self.assertEqual((await client.post("/ext/jev/templates/support/import", headers={"X-User": "alice"})).status, 404)
                imported = await client.post("/ext/jev/recipes", json={"document": starter(), "filename": "support.json"}, headers={"X-User": "alice"})
                self.assertEqual(imported.status, 201)
                own = await imported.json()
                changed = copy.deepcopy(own["document"])
                changed["description"] = "Alice's support recipe"
                changed["name"] = "Alice's new display name"
                saved = await client.put(
                    "/ext/jev/recipes/" + own["id"],
                    json={"document": changed, "revision": 1},
                    headers={"X-User": "alice"},
                )
                self.assertEqual(saved.status, 200)
                self.assertEqual((await saved.json())["id"], "support")
                reopened = await client.post("/ext/jev/recipes", json={"document": starter(), "filename": "support.json"}, headers={"X-User": "alice"})
                self.assertEqual(reopened.status, 409)
                warning = (await reopened.json())["responseStatus"]
                self.assertEqual(warning["errorCode"], "RecipeExistsError")
                self.assertEqual(warning["existingRecipe"], {"id": "support", "revision": 2})
                replaced = await client.post(
                    "/ext/jev/recipes", json={"document": starter(), "filename": "support.json", "replaceRevision": 2}, headers={"X-User": "alice"}
                )
                self.assertEqual(replaced.status, 200)
                self.assertEqual((await replaced.json())["document"], starter())
                self.assertEqual(
                    len((await (await client.get("/ext/jev/recipes", headers={"X-User": "alice"})).json())["items"]), 2
                )
                self.assertEqual((await client.post("/ext/jev/templates/not-a-template/import")).status, 404)
                self.assertEqual((await client.get("/ext/jev/recipes/..%2Fother")).status, 404)
                self.assertEqual((await client.get("/ext/jev/runs/..%2Fother")).status, 404)
                self.assertEqual(
                    (await client.get("/ext/jev/recipes/" + own["id"], headers={"X-User": "bob"})).status, 404
                )
                created = await client.post(
                    "/ext/jev/recipes",
                    json={"document": starter(), "filename": "support.json"},
                    headers={"X-User": "alice"},
                )
                self.assertEqual(created.status, 409)
                created = await client.post(
                    "/ext/jev/recipes",
                    json={"document": starter(), "filename": "support.json", "replaceRevision": 3},
                    headers={"X-User": "alice"},
                )
                self.assertEqual(created.status, 200)
                doc = starter()
                created = await client.post(
                    "/ext/jev/recipes",
                    json={"document": doc, "filename": "another-support.json"},
                    headers={"X-User": "alice"},
                )
                self.assertEqual(created.status, 201)
                row = await created.json()
                other = await client.get("/ext/jev/recipes/" + row["id"], headers={"X-User": "bob"})
                self.assertEqual(other.status, 404)
                broken = starter()
                broken["questions"]["is_bug"]["instructions"] = ""
                response = await client.post("/ext/jev/validate", json={"recipe": broken})
                self.assertEqual(response.status, 400)
                self.assertEqual(
                    (await response.json())["responseStatus"]["errors"][0]["fieldName"], "questions.is_bug.instructions"
                )
            for cleanup in cleanups:
                await cleanup()


class BoundaryTests(IsolatedAsyncioTestCase):
    async def test_chunked_response_success_and_error_boundaries(self):
        recipe = starter()
        payload = compile_request(recipe, recipe["examples"][0]["input"])
        client = DecisionClient(
            SimpleNamespace(
                get_registered_provider=lambda _: SimpleNamespace(
                    api_key="private", headers={"HTTP-Referer": "https://example.com"}
                )
            )
        )
        session = Mock(closed=False)
        client.session = session

        async def chunks(data):
            for start in range(0, len(data), 37):
                yield data[start : start + 37]

        raw = sample_response(recipe)
        for data, expected in (
            (json.dumps(raw).encode(), None),
            (b"<html>invalid</html>", "unreadable"),
            (b'{"answers": [], "cost": 1e400}', "unreadable"),
            (b"[]", "every requested answer"),
            (b"x" * (MAX_RESPONSE + 1), "size limit"),
        ):
            response = SimpleNamespace(
                status=200, content=SimpleNamespace(iter_chunked=lambda _, data=data: chunks(data))
            )
            session.post.return_value.__aenter__ = AsyncMock(return_value=response)
            session.post.return_value.__aexit__ = AsyncMock(return_value=False)
            if expected:
                with self.assertRaisesRegex(DecisionError, expected) as error:
                    await client.decide(payload)
                if data == b"[]":
                    self.assertEqual(error.exception.raw, [])
            else:
                original, normalized = await client.decide(payload)
                self.assertEqual(original, raw)
                self.assertEqual(normalized["urgency"]["score"], 1.99)
        self.assertEqual(session.post.call_count, 5)
        self.assertEqual(session.post.call_args.kwargs["headers"]["HTTP-Referer"], "https://example.com")
        session.post.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError())
        with self.assertRaisesRegex(DecisionError, "may have been processed"):
            await client.decide(payload)
        self.assertEqual(session.post.call_count, 6)

    async def test_invalid_generation_models_never_dispatch(self):
        ctx = SimpleNamespace(
            get_providers=lambda: {
                "images": SimpleNamespace(models={"img": {"id": "image-model", "modalities": {"output": ["image"]}}})
            },
            chat_completion=AsyncMock(),
        )
        for model in ("~typesafe/jev-latest", "image-model"):
            with self.assertRaises(ValidationError):
                await generate(ctx, "alice", {"goal": "Write a recipe", "model": model})
        ctx.chat_completion.assert_not_called()

    async def test_real_api_run_idempotency_isolation_and_snapshot_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            app, cleanups = web.Application(), []
            ctx = SimpleNamespace(
                assert_username=lambda request: request.headers.get("X-User", "alice"),
                get_user_path=lambda user: str(Path(directory) / user),
                get_registered_provider=lambda _: None,
                register_cleanup_handler=cleanups.append,
            )
            for method in ("get", "post", "put", "delete"):
                setattr(
                    ctx,
                    "add_" + method,
                    lambda path, handler, method=method: app.router.add_route(
                        method.upper(), "/ext/jev/" + path, handler
                    ),
                )
            doc = starter()
            raw = sample_response(doc)
            adapter = SimpleNamespace(
                decide=AsyncMock(return_value=(raw, normalize_answers(raw, doc["questions"]))), close=AsyncMock()
            )
            with patch("llms.extensions.jev.DecisionClient", return_value=adapter):
                install(ctx)
            try:
                async with TestClient(TestServer(app)) as client:
                    row = await (await client.post("/ext/jev/recipes", json={"document": doc})).json()
                    body = {
                        "submissionId": "same-submission",
                        "recipe": doc,
                        "input": doc["examples"][0]["input"],
                        "recipeId": row["id"],
                    }
                    submitted = await (await client.post("/ext/jev/runs", json=body)).json()
                    duplicate = await (await client.post("/ext/jev/runs", json=body)).json()
                    self.assertEqual(submitted["id"], duplicate["id"])
                    await asyncio.sleep(0.01)
                    result = await (await client.get("/ext/jev/runs/" + submitted["id"])).json()
                    self.assertEqual(result["status"], "succeeded")
                    self.assertEqual(adapter.decide.call_count, 1)
                    other = await client.get("/ext/jev/runs/" + submitted["id"], headers={"X-User": "bob"})
                    self.assertEqual(other.status, 404)
                    await client.delete("/ext/jev/recipes/" + row["id"])
                    snapshot = await (await client.get("/ext/jev/runs/" + submitted["id"])).json()
                    self.assertEqual(snapshot["recipe"]["questions"], doc["questions"])
                    self.assertEqual(snapshot["recipeRevision"], 1)
                    summary = await (await client.get("/ext/jev/runs")).json()
                    self.assertNotIn("input", summary["items"][0])
                    await client.delete("/ext/jev/runs/" + submitted["id"])
                    self.assertEqual((await client.get("/ext/jev/runs/" + submitted["id"])).status, 404)
                    bad = await client.post("/ext/jev/runs", json={**body, "recipeId": []})
                    self.assertEqual(bad.status, 400)
                    bad = await client.post("/ext/jev/validate", data=b"{ broken")
                    self.assertEqual(bad.status, 400)
                    bad = await client.post("/ext/jev/validate", data=b"x" * 524289)
                    self.assertEqual(bad.status, 400)
                    # Readable IDs carry filename characters through every API reference.
                    from urllib.parse import quote

                    created = await client.post(
                        "/ext/jev/recipes", json={"document": doc, "filename": "Révision #1%.json"}
                    )
                    special_recipe = await created.json()
                    special_body = {**body, "submissionId": "special-name", "recipeId": special_recipe["id"]}
                    special = await (await client.post("/ext/jev/runs", json=special_body)).json()
                    self.assertEqual(special["id"], "Révision #1%-00001")
                    await asyncio.sleep(0.01)
                    url = "/ext/jev/runs/" + quote(special["id"], safe="")
                    self.assertEqual((await (await client.get(url)).json())["status"], "succeeded")
                    cancelled = await client.post(url + "/cancel", json={})
                    self.assertEqual(cancelled.status, 200)
                    self.assertEqual((await (await client.delete(url)).json())["deleted"], 1)
            finally:
                for cleanup in cleanups:
                    await cleanup()


class NumericContractTests(TestCase):
    def test_huge_numbers_do_not_overflow_validation_and_numeric_enum_matches_json_numbers(self):
        doc = starter()
        doc["inputSchema"] = {
            "type": "object",
            "properties": {"value": {"type": "number", "enum": [1, 2]}},
            "required": ["value"],
        }
        doc["examples"] = []
        self.assertEqual(compile_request(doc, {"value": 1.0})["state"]["value"], 1)
        with self.assertRaises(ValidationError):
            compile_request(doc, {"value": 10**400})
        with self.assertRaises(ValidationError):
            compile_request(doc, {"value": True})


class HostIntegrationTests(IsolatedAsyncioTestCase):
    async def test_native_extension_loading_and_served_assets(self):
        import argparse
        import importlib.util

        host = importlib.import_module("llms.main")
        path = Path(__file__).resolve().parents[1] / "llms" / "extensions" / "jev"
        spec = importlib.util.spec_from_file_location("jev", path / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous_app = host.g_app
        application = host.AppExtensions(argparse.Namespace(), {})
        try:
            context = host.ExtensionContext(application, str(path))
            module.__install__(context)
            context.add_static_files(str(path / "ui"))
            context.register_ui_extension("index.mjs")
            self.assertIn({"id": "jev", "path": "/ext/jev/index.mjs"}, application.ui_extensions)
            app = web.Application()
            for method in ("get", "post", "put", "delete"):
                for route, handler, options in getattr(application, "server_add_" + method):
                    app.router.add_route(method.upper(), route, handler, **options)
            async with TestClient(TestServer(app)) as client:
                for filename in ("index.mjs", "JevPage.mjs", "StudioNotice.mjs", "recipeModel.mjs"):
                    response = await client.get("/ext/jev/" + filename)
                    self.assertEqual(response.status, 200, filename)
                    self.assertGreater(len(await response.read()), 100)
        finally:
            for cleanup in application.cleanup_handlers:
                await cleanup()
            host.g_app = previous_app
