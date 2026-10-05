"""Versioned, deliberately small recipe contract. No executable templates or remote refs."""

import copy
import json
import math
import re

MODELS = ["~typesafe/jev-latest", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"]
MAX_BYTES = 512 * 1024
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
RESERVED = {"constructor", "prototype", "__proto__"}
SCHEMA_KEYS = {
    "type",
    "title",
    "description",
    "default",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
}


class ValidationError(ValueError):
    def __init__(self, path, message):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def require(condition, path, message):
    if not condition:
        raise ValidationError(path, message)


def json_copy(value):
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        require(len(encoded.encode()) <= MAX_BYTES, "document", "Keep the document under 512 KB.")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError("document", "Use finite, serializable JSON values.") from None


def key(value, path):
    require(
        isinstance(value, str) and KEY.fullmatch(value) and value not in RESERVED,
        path,
        "Use a letter followed by letters, numbers or underscores (up to 64 characters).",
    )


def text(value, path, maximum=8000, empty=False):
    require(
        isinstance(value, str) and len(value) <= maximum and (empty or value.strip()),
        path,
        f"Enter {'text' if empty else 'nonempty text'} of at most {maximum} characters.",
    )


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def validate_schema(schema, path="inputSchema", depth=0):
    require(isinstance(schema, dict), path, "Expected an input schema object.")
    require(depth <= 4, path, "Use at most four levels of nesting.")
    unknown = set(schema) - SCHEMA_KEYS
    require(not unknown, path, f"Unsupported schema keywords: {', '.join(sorted(unknown))}.")
    kind = schema.get("type")
    require(
        kind in ("object", "string", "number", "integer", "boolean", "array"),
        path + ".type",
        "Choose a supported type.",
    )
    for name in ("title", "description"):
        if name in schema:
            text(schema[name], path + "." + name, empty=True)
    if "format" in schema:
        require(
            kind == "string" and schema["format"] in ("textarea", "date", "email"),
            path + ".format",
            "Supported hints are textarea, date and email.",
        )
    for low, high, allowed in (
        ("minLength", "maxLength", "string"),
        ("minItems", "maxItems", "array"),
        ("minimum", "maximum", "numeric"),
    ):
        for name in (low, high):
            if name in schema:
                v = schema[name]
                require(
                    (kind in ("number", "integer") if allowed == "numeric" else kind == allowed) and finite(v),
                    path + "." + name,
                    "Invalid bound for this type.",
                )
                if allowed != "numeric":
                    require(isinstance(v, int) and v >= 0, path + "." + name, "Use a nonnegative integer.")
        if low in schema and high in schema:
            require(schema[low] <= schema[high], path, "The lower bound exceeds the upper bound.")
    if kind == "object":
        props = schema.get("properties", {})
        require(isinstance(props, dict) and len(props) <= 32, path + ".properties", "Use at most 32 fields per object.")
        required = schema.get("required", [])
        require(
            isinstance(required, list) and all(isinstance(x, str) and x in props for x in required),
            path + ".required",
            "Required fields must exist in properties.",
        )
        require(len(required) == len(set(required)), path + ".required", "Remove duplicate required fields.")
        require(
            isinstance(schema.get("additionalProperties", False), bool),
            path + ".additionalProperties",
            "Use true or false.",
        )
        for name, prop in props.items():
            key(name, path + ".properties." + name)
            validate_schema(prop, path + ".properties." + name, depth + 1)
    else:
        require(
            not set(schema) & {"properties", "required", "additionalProperties"},
            path,
            "Object keywords require an object type.",
        )
    if kind == "array":
        validate_schema(schema.get("items"), path + ".items", depth + 1)
        require(
            schema["items"]["type"] not in ("array", "object"),
            path + ".items",
            "Arrays currently support primitive values.",
        )
    else:
        require("items" not in schema, path, "items requires an array type.")
    if "enum" in schema:
        values = schema["enum"]
        require(
            kind not in ("object", "array") and isinstance(values, list) and 1 <= len(values) <= 255,
            path + ".enum",
            "Use 1–255 primitive options.",
        )
        require(len({json.dumps(v) for v in values}) == len(values), path + ".enum", "Remove duplicate options.")
        for v in values:
            validate_input({k: v2 for k, v2 in schema.items() if k not in ("enum", "default")}, v, path + ".enum")
    if "default" in schema:
        validate_input(
            {k: v for k, v in schema.items() if k != "default"}, schema["default"], path + ".default", required=False
        )


def validate_input(schema, value, path="input", required=True):
    kind = schema["type"]
    if kind == "object":
        require(isinstance(value, dict), path, "Expected an object.")
        props = schema.get("properties", {})
        require(
            schema.get("additionalProperties", False) or not set(value) - set(props),
            path,
            "Remove unknown input fields.",
        )
        for name in schema.get("required", []):
            require(name in value and value[name] is not None, path + "." + name, "This field is required.")
        for name, v in value.items():
            if name in props:
                validate_input(props[name], v, path + "." + name, name in schema.get("required", []))
    elif kind == "array":
        require(isinstance(value, list), path, "Expected a list.")
        require(
            schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 500),
            path,
            "List length is outside the allowed range.",
        )
        for i, v in enumerate(value):
            validate_input(schema["items"], v, f"{path}.{i}")
    elif kind == "string":
        require(isinstance(value, str), path, "Expected text.")
        require(not required or value.strip(), path, "This field is required.")
        # Empty optional/default values are valid even when a filled value has length constraints.
        if value or required:
            require(
                schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_BYTES),
                path,
                "Text length is outside the allowed range.",
            )
    elif kind == "boolean":
        require(isinstance(value, bool), path, "Expected true or false.")
    else:
        require(finite(value) and (kind != "integer" or value == int(value)), path, f"Expected a finite {kind}.")
        require(
            schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf),
            path,
            "Number is outside the allowed range.",
        )
    if "enum" in schema:
        require(
            any((finite(value) and finite(v) or type(value) is type(v)) and value == v for v in schema["enum"]),
            path,
            "Choose one of the listed options.",
        )


def validate_recipe(document):
    doc = json_copy(document)
    require(isinstance(doc, dict), "recipe", "Expected a recipe object.")
    allowed = {
        "schemaVersion",
        "name",
        "description",
        "tags",
        "content",
        "decisionModel",
        "inputSchema",
        "state",
        "questions",
        "presentation",
        "examples",
    }
    require(not set(doc) - allowed, "recipe", "Remove unsupported or server-owned recipe fields.")
    require(
        doc.get("schemaVersion") == 1 and type(doc["schemaVersion"]) is int,
        "schemaVersion",
        "Only recipe version 1 is supported.",
    )
    text(doc.get("name"), "name", 120)
    text(doc.get("description", ""), "description", 2000, empty=True)
    if "content" in doc:
        text(doc["content"], "content", 40, empty=True)
    tags = doc.get("tags", [])
    # Keep older mixed metadata valid for existing files, shares and pending journals.
    # Editing metadata writes the separate content field and uses the new three-tag limit.
    tag_limit = 3 if "content" in doc else 12
    require(isinstance(tags, list) and len(tags) <= tag_limit, "tags", f"Use up to {tag_limit} tags.")
    for tag in tags:
        text(tag, "tags", 40)
    require(doc.get("decisionModel") in MODELS, "decisionModel", "Choose a supported Jev decision model.")
    validate_schema(doc.get("inputSchema"))
    require(doc["inputSchema"]["type"] == "object", "inputSchema.type", "The form root must be an object.")
    state = doc.get("state", {"mode": "object"})
    require(isinstance(state, dict) and state.get("mode") in ("object", "text"), "state", "Use object or text mode.")
    require(not set(state) - {"mode", "field"}, "state", "Unsupported state mapping.")
    if state["mode"] == "text":
        field = state.get("field")
        require(
            isinstance(field, str) and doc["inputSchema"].get("properties", {}).get(field, {}).get("type") == "string",
            "state.field",
            "Choose a top-level text field.",
        )
    questions = doc.get("questions")
    require(isinstance(questions, dict) and 1 <= len(questions) <= 32, "questions", "Use 1–32 independent questions.")
    for name, q in questions.items():
        path = "questions." + name
        key(name, path)
        require(
            isinstance(q, dict) and not set(q) - {"type", "instructions", "criteria"},
            path,
            "Use type, instructions and criteria only.",
        )
        text(q.get("instructions"), path + ".instructions")
        kind, criteria = q.get("type"), q.get("criteria")
        require(kind in ("choice", "score", "noul"), path + ".type", "Choose Choice, Score or Noul.")
        if kind == "score":
            require(
                isinstance(criteria, list) and 2 <= len(criteria) <= 10,
                path + ".criteria",
                "Use 2–10 ordered descriptions.",
            )
            for item in criteria:
                text(item, path + ".criteria")
        elif kind == "choice":
            require(
                isinstance(criteria, dict) and 2 <= len(criteria) <= 255, path + ".criteria", "Use 2–255 named options."
            )
            for option, description in criteria.items():
                key(option, path + ".criteria." + option)
                text(description, path + ".criteria." + option)
        elif criteria is not None:
            require(
                isinstance(criteria, dict) and set(criteria) == {"true", "false"},
                path + ".criteria",
                "Describe both true and false.",
            )
            for description in criteria.values():
                text(description, path + ".criteria")
    presentation = doc.get("presentation", {"questions": {}})
    require(
        isinstance(presentation, dict) and not set(presentation) - {"questions"},
        "presentation",
        "Expected question labels.",
    )
    labels = presentation.get("questions", {})
    require(
        isinstance(labels, dict) and not set(labels) - set(questions),
        "presentation.questions",
        "Labels must refer to existing questions.",
    )
    for name, spec in labels.items():
        require(
            isinstance(spec, dict) and not set(spec) - {"label", "optionLabels"},
            "presentation." + name,
            "Use label and optionLabels.",
        )
        if "label" in spec:
            text(spec["label"], "presentation." + name + ".label", 200)
        if "optionLabels" in spec:
            options = spec["optionLabels"]
            require(
                questions[name]["type"] == "choice"
                and isinstance(options, dict)
                and not set(options) - set(questions[name]["criteria"]),
                "presentation." + name,
                "Labels must refer to existing choice options.",
            )
            for label in options.values():
                text(label, "presentation." + name, 200)
    examples = doc.get("examples", [])
    require(isinstance(examples, list) and len(examples) <= 30, "examples", "Use up to 30 example cases.")
    ids = set()
    for i, example in enumerate(examples):
        path = f"examples.{i}"
        require(
            isinstance(example, dict)
            and not set(example) - {"id", "label", "input", "expected", "provenance", "notes", "execution"},
            path,
            "Unsupported example fields.",
        )
        text(example.get("id"), path + ".id", 80)
        require(example["id"] not in ids, path + ".id", "Example IDs must be unique.")
        ids.add(example["id"])
        text(example.get("label"), path + ".label", 120)
        validate_input(doc["inputSchema"], example.get("input"), path + ".input")
        expected = example.get("expected", {})
        require(
            isinstance(expected, dict) and not set(expected) - set(questions),
            path + ".expected",
            "Expectations must refer to existing questions.",
        )
        for name, value in expected.items():
            q = questions[name]
            valid = (
                isinstance(value, str) and value in q["criteria"]
                if q["type"] == "choice"
                else isinstance(value, bool)
                if q["type"] == "noul"
                else finite(value) and 0 <= value <= len(q["criteria"]) - 1
            )
            require(valid, path + ".expected." + name, "Expected answer does not match this question's options/scale.")
        require(
            example.get("provenance", "authored") in ("authored", "ai-suggested", "user-reviewed"),
            path + ".provenance",
            "Choose expectation provenance.",
        )
        if "notes" in example:
            text(example["notes"], path + ".notes", 2000, empty=True)
        if "execution" in example:
            from .recorded import validate_execution
            from .storage import document_hash

            # Examples are documentation, never part of the executable request. Strip
            # them to avoid recursively validating recorded examples during compilation.
            execution = validate_execution({**doc, "examples": []}, example["execution"])
            require(
                document_hash(execution["input"]) == document_hash(example["input"]),
                path + ".execution.input",
                "Recorded input must match the example.",
            )
    return doc


def compile_request(document, inputs):
    doc = validate_recipe(document)
    inputs = json_copy(inputs)
    validate_input(doc["inputSchema"], inputs)
    mapping = doc.get("state", {"mode": "object"})
    state = inputs if mapping["mode"] == "object" else inputs.get(mapping["field"], "")
    require(state != "", "input", "Provide text to evaluate.")
    return {"model": doc["decisionModel"], "state": copy.deepcopy(state), "questions": copy.deepcopy(doc["questions"])}
