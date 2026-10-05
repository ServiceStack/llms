"""Portable import settings, independent of a store's database identity."""

import os

from . import crawl, ingest

SOURCE_FIELDS = ("name", "type", "category", "extract", "chunking", "volatile", "onDelete", "extractorVer")


def load(path, fallback=None):
    path = ingest.resolve_path(path)
    if os.path.basename(path) != crawl.MANIFEST or not os.path.isfile(path):
        raise ValueError("Select an existing import.json")
    cfg = crawl.read_json(path)
    settings = cfg.get("source") or {}
    if not isinstance(settings, dict):
        raise ValueError("import.json source must be an object")
    row = dict(fallback or {})
    if "source" in cfg:
        for key in SOURCE_FIELDS:
            row.pop(key, None)
    row.update({key: settings[key] for key in SOURCE_FIELDS if key in settings})
    row.setdefault("type", "folder")
    if row["type"] not in ingest.SOURCE_TYPES:
        raise ValueError(f"Unknown source type '{row['type']}'")
    config = dict(settings.get("config") or ((fallback or {}).get("config") if "source" not in cfg else {}) or {})
    folder = os.path.dirname(path)
    source_path = config.get("path") or "."
    config["path"] = ingest.resolve_path(os.path.join(folder, os.path.expanduser(source_path)))
    config["manifestPath"] = path
    config["metadataSpecified"] = False
    for key in ("include", "exclude", "ignore"):
        config[key] = ingest.file_patterns(config.get(key, cfg.get(key)))
    row["config"] = config
    row["rules"] = cfg.get("metadata") or {"defaults": {}, "rules": []}
    # Older manifests kept the destination among metadata defaults. Category derivation
    # needs an explicit prefix or it overwrites that default with the folder structure.
    if "category" not in settings and "category" in row["rules"].get("defaults", {}):
        row["category"] = {**(row.get("category") or {}), "prefix": row["rules"]["defaults"]["category"]}
    if not row.get("name"):
        row["name"] = "Import " + (os.path.basename(os.path.normpath(config["path"])) or os.path.basename(folder))
    return row


def save(source, options=None):
    config = dict(source.get("config") or {})
    folder = ingest.resolve_path(config["path"])
    manifest_dir = folder if source.get("type") == "folder" else os.path.dirname(folder)
    path = config.pop("manifestPath", None) or os.path.join(manifest_dir, crawl.MANIFEST)
    path = ingest.resolve_path(path)
    cfg = crawl.read_json(path)
    config.pop("metadataSpecified", None)
    config.pop("saved", None)
    config["path"] = os.path.relpath(folder, os.path.dirname(path)).replace("\\", "/")
    settings = {key: source[key] for key in SOURCE_FIELDS if key in source}
    settings["config"] = config
    cfg.update({"version": 1, "source": settings})
    if options is not None:
        if "crawl" in options:
            crawl.validate_crawl_rules((options["crawl"] or {}).get("rules") or [])
            cfg["crawl"] = options["crawl"]
        if "transforms" in options:
            crawl.validate_transforms(options["transforms"] or [])
            cfg["transforms"] = options["transforms"]
    # Empty UI metadata means use the existing hierarchical manifests.
    if (source.get("config") or {}).get("metadataSpecified") or "metadata" not in cfg:
        cfg["metadata"] = source.get("rules") or {"defaults": {}, "rules": []}
    crawl.write_json(path, cfg)
    return path
