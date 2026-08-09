"""
PDF Studio - edit typst (.typ) templates with a sidecar .json data file and preview the compiled PDF live.

Requires the `typst` CLI (https://typst.app) to be available on PATH, otherwise the extension disables itself.
Templates are stored in ~/.llms/user/<user>/pdf
"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from aiohttp import web

TYPST = None
_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(_DIR, "examples")
# shared styles every template imports, plus the document that shows what it does
LIB_NAME = "lib.typ"  # legacy root library, still supported
LIB_DIR = "lib"
DEFAULT_LIB_NAME = "lib/v1.typ"
LIB_PREVIEW = "lib/v1.preview.typ"
# attachments: screenshots or rasterised PDF pages the model reads to build a template from
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\s]+$")
PROMPTS_DIR = os.path.join(_DIR, "prompts")
RENDER_TIMEOUT = 30
MAX_PDF_BYTES = 50 * 1024 * 1024
# images a template can #image() - typst reads png, jpeg, gif, svg and webp
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MAX_ASSET_BYTES = 20 * 1024 * 1024
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"<svg", b"<?xml")
_fonts_cache = {}

# typst --diagnostic-format short, e.g: /path/to/invoice.typ:3:5: error: unknown variable: total
DIAGNOSTIC_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<severity>error|warning): (?P<message>.*)$")

# ```typst path=invoice.typ ... ``` blocks returned by the AI edit prompt.
# Not anchored to the start of a line: models often open the fence straight after their summary sentence.
CODE_BLOCK_RE = re.compile(r"```([^\n`]*)\n(.*?)[ \t]*```", re.DOTALL)
BLOCK_PATH_RE = re.compile(r"path\s*=\s*[\"']?([^\s\"']+)")


# fenced languages a model is likely to tag an untagged block with, mapped to the file extension they imply
BLOCK_LANGS = {
    "typst": ".typ",
    "typ": ".typ",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "toml": ".toml",
    "csv": ".csv",
    "xml": ".xml",
}


def parse_edited_files(answer: str, default_path: str, known_paths=()) -> dict:
    """Pull code blocks out of the model's answer, keyed by relative path"""
    edits = {}
    blocks = CODE_BLOCK_RE.findall(answer or "")
    for info, content in blocks:
        match = BLOCK_PATH_RE.search(info)
        path = match.group(1) if match else None

        if not path:
            # tolerate ```typst invoice.typ
            candidates = [word for word in info.split() if "." in word and not word.startswith(".")]
            path = candidates[0] if candidates else None

        if not path:
            # untagged block: fall back to the file we sent whose extension matches the fence language,
            # so a bare ```typst block is still recognised as an edit to the template
            ext = BLOCK_LANGS.get(info.split()[0].lower() if info.split() else "")
            if ext == ".typ":
                path = default_path if default_path not in edits else None
            elif ext:
                path = next((p for p in known_paths if p.endswith(ext) and p not in edits), None)
            elif len(blocks) == 1:
                path = default_path

        if path:
            edits[path.replace("\\", "/").lstrip("/")] = content
    return edits


def strip_code_blocks(answer: str) -> str:
    return CODE_BLOCK_RE.sub("", answer or "").strip()


def is_safe_path(base_path: str, requested_path: str) -> bool:
    """Check if the requested path is safely within the base path."""
    base = Path(base_path).resolve()
    target = Path(requested_path).resolve()
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def is_hidden(rel_path: str) -> bool:
    return any(part.startswith(".") for part in rel_path.replace("\\", "/").split("/") if part)


def sidecar_path(rel_path: str) -> str:
    """The <name>.json data file that belongs to a <name>.typ template"""
    return os.path.splitext(rel_path)[0] + ".json"


def typst_ref(rel_path: str) -> str:
    """
    How a template refers to a file: relative to the templates root, with forward slashes.

    Data files are loaded through lib.typ's load-data(), and typst resolves a json() path relative
    to the file that calls it - lib.typ at the root - not to the template passing it. A path
    relative to the template only works for templates in the root folder.
    """
    return rel_path.replace("\\", "/").lstrip("/")


def install(ctx):
    global TYPST
    TYPST = shutil.which("typst")
    if not TYPST:
        ctx.log("typst not found in PATH, PDF Studio disabled")
        ctx.disabled = True
        return
    ctx.log(f"Using {TYPST} for PDF rendering")

    # Serialize renders per user, they all share the same mirror dir
    render_locks = {}

    def pdf_root(user: Optional[str]) -> str:
        path = os.path.join(ctx.get_user_path(user), "pdf")
        os.makedirs(path, exist_ok=True)
        return path

    def mirror_root(user: Optional[str]) -> str:
        path = ctx.get_cache_path(os.path.join("pdf", user or "default"))
        os.makedirs(path, exist_ok=True)
        return path

    def resolve(root: str, rel_path: Optional[str], must_exist=False) -> str:
        """Validate a client supplied relative path and return its absolute path within root"""
        if not rel_path:
            raise Exception("Missing 'path'")
        rel_path = rel_path.replace("\\", "/").lstrip("/")
        if is_hidden(rel_path):
            raise Exception("Invalid file path")
        full_path = os.path.join(root, rel_path)
        if not is_safe_path(root, full_path):
            raise Exception("Invalid file path")
        if must_exist and not os.path.exists(full_path):
            raise Exception(f"'{rel_path}' not found")
        return full_path

    def read_text(path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_text(path: str, content: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def seed_examples(root: str):
        """Copy the starter templates into an empty pdf folder so there's something to look at"""
        if not os.path.isdir(EXAMPLES_DIR):
            return
        if os.listdir(root):
            seed_library(root)
            upgrade_bundled_examples(root)
            return
        shutil.copytree(EXAMPLES_DIR, root, dirs_exist_ok=True)
        ctx.log(f"Seeded example templates in {root}")

    def lib_import(rel_path: str) -> str:
        """The versioned library sits below the root, so nested templates walk back to it."""
        depth = len(os.path.dirname(rel_path).split("/")) if os.path.dirname(rel_path) else 0
        return "../" * depth + DEFAULT_LIB_NAME

    def lib_data_ref(rel_path: str) -> str:
        """load-data executes in lib/v1.typ, one directory below the artifact root."""
        return "../" + typst_ref(rel_path)

    def seed_library(root: str):
        """Every workspace keeps a versioned library baseline. Legacy root lib.typ remains untouched."""
        lib = os.path.join(root, DEFAULT_LIB_NAME)
        if os.path.exists(lib):
            return
        for name in (DEFAULT_LIB_NAME, LIB_PREVIEW):
            src = os.path.join(EXAMPLES_DIR, name)
            if os.path.isfile(src):
                dst = os.path.join(root, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        ctx.log(f"Restored {DEFAULT_LIB_NAME} in {root}")

    def upgrade_bundled_examples(root: str):
        """Upgrade known, unchanged starter files without overwriting user edits."""
        path = os.path.join(root, "invoice.json")
        bundled_path = os.path.join(EXAMPLES_DIR, "invoice.json")
        if not os.path.isfile(path) or not os.path.isfile(bundled_path):
            return
        bundled = read_text(bundled_path)
        previous = bundled.replace('"date": "2025-03-15"', '"date": "15 March 2025"')
        if read_text(path) == previous:
            shutil.copy2(bundled_path, path)
            ctx.log(f"Upgraded unchanged example invoice data in {path}")

    def file_tree(root: str, dir_path: str, rel_prefix: str = "") -> list:
        nodes = []
        for name in sorted(os.listdir(dir_path)):
            if name.startswith("."):
                continue
            full_path = os.path.join(dir_path, name)
            rel_path = f"{rel_prefix}{name}"
            if os.path.isdir(full_path):
                nodes.append(
                    {
                        "name": name,
                        "path": rel_path,
                        "isFile": False,
                        "children": file_tree(root, full_path, f"{rel_path}/"),
                    }
                )
            else:
                stat = os.stat(full_path)
                nodes.append(
                    {
                        "name": name,
                        "path": rel_path,
                        "isFile": True,
                        "ext": os.path.splitext(name)[1].lower(),
                        "size": stat.st_size,
                        "modified": int(stat.st_mtime * 1000),
                    }
                )
        nodes.sort(key=lambda x: (x["isFile"], x["name"].lower()))
        return nodes

    def sync_tree(src_dir: str, dst_dir: str):
        """Mirror src_dir into dst_dir, copying only what changed and removing what no longer exists"""
        os.makedirs(dst_dir, exist_ok=True)
        src_names = {name for name in os.listdir(src_dir) if not name.startswith(".")}

        for name in os.listdir(dst_dir):
            if name in src_names:
                continue
            stale = os.path.join(dst_dir, name)
            shutil.rmtree(stale, ignore_errors=True) if os.path.isdir(stale) else os.remove(stale)

        for name in src_names:
            src = os.path.join(src_dir, name)
            dst = os.path.join(dst_dir, name)
            if os.path.isdir(src):
                if os.path.isfile(dst):
                    os.remove(dst)
                sync_tree(src, dst)
            else:
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                src_stat = os.stat(src)
                if os.path.exists(dst):
                    dst_stat = os.stat(dst)
                    if dst_stat.st_size == src_stat.st_size and dst_stat.st_mtime >= src_stat.st_mtime:
                        continue
                shutil.copy2(src, dst)

    def parse_diagnostics(stderr: str, mirror: str) -> list:
        diagnostics = []
        for line in stderr.splitlines():
            match = DIAGNOSTIC_RE.match(line.strip())
            if match:
                # typst reports paths relative to the cwd, map them back to the user's templates folder
                file_path = os.path.abspath(match.group("file"))
                if is_safe_path(mirror, file_path):
                    file_path = os.path.relpath(os.path.realpath(file_path), os.path.realpath(mirror))
                diagnostics.append(
                    {
                        "file": file_path.replace("\\", "/"),
                        "line": int(match.group("line")),
                        "col": int(match.group("col")),
                        "severity": match.group("severity"),
                        "message": match.group("message"),
                    }
                )
            elif line.strip():
                diagnostics.append({"severity": "error", "message": line.strip()})
        return diagnostics

    async def list_files(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        seed_examples(root)
        return web.json_response({"path": root, "files": file_tree(root, root)})

    ctx.add_get("files", list_files)

    def not_found(rel_path):
        return web.json_response(
            {"responseStatus": {"errorCode": "NotFound", "message": f"'{rel_path}' not found"}}, status=404
        )

    async def get_file(request):
        """Read a text file (template or one of the resources it references)"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = request.query.get("path")
        full_path = resolve(root, rel_path)
        if not os.path.isfile(full_path):
            # the UI asks for resources a template references before they necessarily exist
            return not_found(rel_path)
        return web.json_response(
            {
                "path": rel_path,
                "content": read_text(full_path),
                "modified": int(os.stat(full_path).st_mtime * 1000),
            }
        )

    ctx.add_get("file", get_file)

    async def get_raw(request):
        """Serve a referenced resource as-is, for previewing images the template uses"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = request.query.get("path")
        full_path = resolve(root, rel_path)
        if not os.path.isfile(full_path):
            return not_found(rel_path)
        return web.FileResponse(full_path, headers={"Cache-Control": "no-store"})

    ctx.add_get("raw", get_raw)

    async def save_file(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        rel_path = body.get("path")
        content = body.get("content")
        if content is None:
            raise Exception("Missing 'content'")
        full_path = resolve(root, rel_path)
        write_text(full_path, content)
        return web.json_response({"path": rel_path, "modified": int(os.stat(full_path).st_mtime * 1000)})

    ctx.add_post("file", save_file)

    async def save_pdf(request):
        """Store the exact PDF bytes the preview produced under saved/<template>/"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = request.query.get("path")
        if not rel_path or not rel_path.lower().endswith(".pdf"):
            raise Exception("Path must be a .pdf")
        full_path = resolve(root, rel_path)
        data = await request.read()
        if not data.startswith(b"%PDF"):
            raise Exception("That isn't a PDF")
        if len(data) > MAX_PDF_BYTES:
            raise Exception(f"PDF is too large: {len(data) // 1024}KB")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)
        return web.json_response(
            {"path": rel_path, "size": len(data), "modified": int(os.stat(full_path).st_mtime * 1000)}
        )

    ctx.add_post("pdf", save_pdf)

    async def upload_asset(request):
        """Store an uploaded image in the templates folder so templates can #image() it"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = request.query.get("path") or ""
        ext_name = os.path.splitext(rel_path)[1].lower()
        if ext_name not in ASSET_EXTS:
            raise Exception(f"'{ext_name or rel_path}' isn't a supported image ({', '.join(sorted(ASSET_EXTS))})")
        full_path = resolve(root, rel_path)
        if os.path.exists(full_path):
            return web.json_response(
                {"responseStatus": {"errorCode": "AlreadyExists", "message": f"'{rel_path}' already exists"}},
                status=409,
            )
        data = await request.read()
        if not data:
            raise Exception("No image data")
        if len(data) > MAX_ASSET_BYTES:
            raise Exception(f"Image is too large: {len(data) // 1024}KB, at most {MAX_ASSET_BYTES // 1024}KB")
        if ext_name != ".svg" and not any(data.startswith(magic) for magic in IMAGE_MAGIC):
            raise Exception("That doesn't look like an image")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)
        return web.json_response({"path": rel_path, "size": len(data)})

    ctx.add_post("asset", upload_asset)

    async def create_template(request):
        """Create a new .typ template (and optionally its .json sidecar) from the starter example"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        rel_path = body.get("path") or ""
        if not rel_path.endswith(".typ"):
            rel_path += ".typ"
        full_path = resolve(root, rel_path)
        if os.path.exists(full_path):
            return web.json_response(
                {"responseStatus": {"errorCode": "AlreadyExists", "message": f"'{rel_path}' already exists"}},
                status=409,
            )

        name = os.path.splitext(os.path.basename(rel_path))[0]
        rel_data = sidecar_path(rel_path)
        with_data = body.get("withData", True)
        if with_data:
            write_text(
                resolve(root, rel_data),
                json.dumps(
                    {"title": name.replace("-", " ").replace("_", " ").title(), "body": "Hello, World!"}, indent=2
                )
                + "\n",
            )
            write_text(
                full_path,
                f'#import "{lib_import(rel_path)}": *\n\n'
                # load-data is defined in lib/v1.typ, so its data path starts one level above lib/
                f'#let data = load-data("{lib_data_ref(rel_data)}")\n\n'
                "#show: theme\n\n"
                "#title-block(data.title)\n\n"
                "#data.body\n",
            )
        else:
            write_text(
                full_path,
                f'#import "{lib_import(rel_path)}": *\n\n#show: theme\n\n'
                f"#title-block(\"{name}\")\n\nHello, World!\n",
            )

        return web.json_response({"path": rel_path, "data": rel_data if with_data else None})

    ctx.add_post("create", create_template)

    async def create_folder(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        full_path = resolve(root, body.get("path"))
        os.makedirs(full_path, exist_ok=True)
        return web.json_response({"path": body.get("path")})

    ctx.add_post("folder", create_folder)

    def companions(root: str, rel_path: str) -> list:
        """Siblings that belong to the same document: invoice.json, invoice.ui.json, v1.preview.typ"""
        rel_dir = os.path.dirname(rel_path)
        stem = os.path.basename(rel_path).split(".")[0]
        full_dir = os.path.dirname(resolve(root, rel_path))
        if not os.path.isdir(full_dir):
            return []
        names = sorted(
            name
            for name in os.listdir(full_dir)
            if name != os.path.basename(rel_path)
            and name.startswith(stem + ".")
            and os.path.isfile(os.path.join(full_dir, name))
        )
        return [os.path.join(rel_dir, name).replace("\\", "/") if rel_dir else name for name in names]

    def all_templates(root: str) -> list:
        """Every .typ in the folder, relative to it"""
        out = []
        for dir_path, dir_names, file_names in os.walk(root):
            dir_names[:] = [d for d in dir_names if not d.startswith(".")]
            for name in file_names:
                if name.endswith(".typ"):
                    rel = os.path.relpath(os.path.join(dir_path, name), root).replace("\\", "/")
                    out.append(rel)
        return sorted(out)

    def normalize_rel_path(path: Optional[str]) -> str:
        return (path or "").replace("\\", "/").lstrip("/")

    def is_library_template(rel_path: str) -> bool:
        rel_path = normalize_rel_path(rel_path)
        if not rel_path.lower().endswith(".typ"):
            return False
        if rel_path.lower() == LIB_NAME:
            return True
        return rel_path.lower().startswith(LIB_DIR + "/") and ".preview" not in os.path.splitext(
            os.path.basename(rel_path)
        )[0].lower()

    def library_templates(root: str) -> list:
        return [rel for rel in all_templates(root) if rel.lower().startswith(LIB_DIR + "/") and is_library_template(rel)]

    def strip_typst_comments(source: str) -> str:
        return re.sub(r"//.*$|/\*[\s\S]*?\*/", "", source, flags=re.MULTILINE)

    def find_imports(root: str, rel_path: str) -> list:
        source = strip_typst_comments(read_text(resolve(root, rel_path, must_exist=True)))
        rel_dir = os.path.dirname(rel_path)
        imports = []
        for target in re.findall(r'#(?:import|include)\s+"([^"]+)"', source):
            if target.startswith("@"):
                continue
            normalized = os.path.normpath(os.path.join(rel_dir, target)).replace("\\", "/").lstrip("/")
            if normalized not in imports:
                imports.append(normalized)
        return imports

    def find_dependants(root: str, target: str) -> list:
        target = normalize_rel_path(target)
        templates = all_templates(root)
        imports = {rel: find_imports(root, rel) for rel in templates}
        affected = {target}
        changed = True
        while changed:
            changed = False
            for template, dependencies in imports.items():
                if template not in affected and any(dep in affected for dep in dependencies):
                    affected.add(template)
                    changed = True
        affected.discard(target)
        return sorted(affected, key=str.lower)

    def blocked(message: str, error_code: str):
        return web.json_response(
            {"responseStatus": {"errorCode": error_code, "message": message}}, status=409
        )

    def conflict(rel_path: str):
        return web.json_response(
            {"responseStatus": {"errorCode": "AlreadyExists", "message": f"'{rel_path}' already exists"}},
            status=409,
        )

    def assert_operation_name(rel_from: str, rel_to: str):
        if os.path.dirname(rel_from) != os.path.dirname(rel_to):
            raise Exception("Rename and duplicate accept a new file name, not a destination path")
        name = os.path.basename(rel_to)
        if not name.strip() or name in (".", "..") or name.startswith("."):
            raise Exception(f"Invalid file name '{name}'")

    def companion_moves(root: str, rel_from: str, rel_to: str) -> list:
        moves = [(rel_from, rel_to)]
        to_stem = os.path.basename(rel_to)[: -len(".typ")]
        to_dir = os.path.dirname(rel_to)
        from_stem_len = len(os.path.basename(rel_from).split(".")[0])
        for rel_other in companions(root, rel_from):
            suffix = os.path.basename(rel_other)[from_stem_len:]
            moves.append((rel_other, f"{to_dir}/{to_stem}{suffix}" if to_dir else to_stem + suffix))
        return moves

    async def get_dependencies(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = normalize_rel_path(request.query.get("path"))
        resolve(root, rel_path, must_exist=True)
        return web.json_response({"path": rel_path, "dependencies": find_dependants(root, rel_path)})

    ctx.add_get("dependencies", get_dependencies)

    async def duplicate_file(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        rel_from = normalize_rel_path(body.get("from"))
        rel_to = normalize_rel_path(body.get("to"))
        resolve(root, rel_from, must_exist=True)
        assert_operation_name(rel_from, rel_to)
        if not rel_from.lower().endswith(".typ") or not rel_to.lower().endswith(".typ"):
            raise Exception("PDF templates must use a .typ name")
        copies = companion_moves(root, rel_from, rel_to)
        for _, destination in copies:
            if os.path.exists(resolve(root, destination)):
                return conflict(destination)
        created = []
        try:
            for source, destination in copies:
                destination_path = resolve(root, destination)
                os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                shutil.copy2(resolve(root, source, must_exist=True), destination_path)
                created.append(destination)
            for rel_path in (path for path in created if path.lower().endswith(".typ")):
                retarget_references(root, rel_path, copies)
        except Exception:
            for rel_path in created:
                path = resolve(root, rel_path)
                if os.path.isfile(path):
                    os.remove(path)
            raise
        return web.json_response({"path": rel_to, "copied": created})

    ctx.add_post("duplicate", duplicate_file)

    def retarget_references(root: str, rel_path: str, renames: list, imports_only: bool = False):
        """
        Point a template at renamed files: json("invoice.json"), #import "lib.typ".

        The renamed template gets every quoted reference updated - its own data and schema. Other
        templates only get their `#import`/`#include` lines touched, since a mention of another
        document's data file is almost always an example in a comment rather than a real reference.
        """
        full_path = resolve(root, rel_path)
        text = read_text(full_path)
        updated = text
        for old_rel, new_rel in renames:
            refs = {typst_ref(old_rel): typst_ref(new_rel), os.path.basename(old_rel): os.path.basename(new_rel)}
            for old_ref, new_ref in refs.items():
                if old_ref == new_ref:
                    continue
                if imports_only:
                    updated = re.sub(
                        r'(#(?:import|include)\s+")' + re.escape(old_ref) + r'(")',
                        r'\g<1>' + new_ref.replace("\\", "\\\\") + r'\g<2>',
                        updated,
                    )
                else:
                    # only inside a string literal, so prose that happens to say the name is left alone
                    updated = updated.replace(f'"{old_ref}"', f'"{new_ref}"')
        if updated != text:
            write_text(full_path, updated)
            return True
        return False

    async def rename_file(request):
        """Rename a template along with the files that belong to it, fixing the references between them"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        rel_from = normalize_rel_path(body.get("from"))
        rel_to = normalize_rel_path(body.get("to"))
        resolve(root, rel_from, must_exist=True)  # validates it exists and stays inside the folder
        assert_operation_name(rel_from, rel_to)
        to_path = resolve(root, rel_to)
        if os.path.exists(to_path):
            return conflict(rel_to)

        if is_library_template(rel_from):
            dependants = find_dependants(root, rel_from)
            if dependants:
                return blocked(
                    f"Cannot rename '{rel_from}' because it is referenced by: {', '.join(dependants)}",
                    "LibraryInUse",
                )

        renames = [(rel_from, rel_to)]
        if rel_from.endswith(".typ") and rel_to.endswith(".typ"):
            renames = companion_moves(root, rel_from, rel_to)
            for _, destination in renames[1:]:
                if os.path.exists(resolve(root, destination)):
                    return conflict(destination)

        os.makedirs(os.path.dirname(to_path), exist_ok=True)
        for old_rel, new_rel in renames:
            os.rename(resolve(root, old_rel), resolve(root, new_rel))

        # the renamed templates in full, every other one's imports - so a renamed lib.typ doesn't
        # break the documents importing it
        renamed_paths = {new for _, new in renames}
        updated = [
            rel
            for rel in all_templates(root)
            if retarget_references(root, rel, renames, imports_only=rel not in renamed_paths)
        ]

        return web.json_response(
            {
                "path": rel_to,
                "data": next((new for old, new in renames if new.endswith(".json") and not new.endswith(".ui.json")), None),
                "renamed": [{"from": old, "to": new} for old, new in renames],
                "updated": updated,
            }
        )

    ctx.add_post("rename", rename_file)

    async def delete_file(request):
        user = ctx.assert_username(request)
        root = pdf_root(user)
        rel_path = request.query.get("path")
        full_path = resolve(root, rel_path, must_exist=True)
        deleted = [rel_path]

        normalized = normalize_rel_path(rel_path)
        if os.path.isdir(full_path) and any(
            rel.lower().startswith(normalized.lower() + "/") for rel in library_templates(root)
        ):
            return blocked(
                "Delete library templates individually so their dependencies can be checked", "LibraryDirectory"
            )

        if is_library_template(normalized):
            libraries = library_templates(root)
            if normalized.lower().startswith(LIB_DIR + "/") and len(libraries) <= 1:
                return blocked(
                    f"Cannot delete '{rel_path}' because at least one lib/*.typ template is required", "LastLibrary"
                )
            dependants = find_dependants(root, normalized)
            if dependants:
                return blocked(
                    f"Cannot delete '{rel_path}' because it is referenced by: {', '.join(dependants)}", "LibraryInUse"
                )

        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
            # the whole document goes with it: its data, schema, preview, any <stem>.* image
            if request.query.get("sidecar") == "true" and rel_path.endswith(".typ"):
                for rel_other in companions(root, rel_path):
                    other = resolve(root, rel_other)
                    if os.path.isfile(other):
                        os.remove(other)
                        deleted.append(rel_other)

        # prune empty parent dirs
        parent = os.path.dirname(full_path)
        while is_safe_path(root, parent) and os.path.realpath(parent) != os.path.realpath(root):
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            else:
                break

        return web.json_response({"deleted": deleted})

    ctx.add_delete("file", delete_file)

    async def compile_pdf(user: Optional[str], rel_path: str, overlay: dict):
        """Compile rel_path with the given unsaved buffers overlaid -> (pdf_bytes|None, diagnostics)"""
        root = pdf_root(user)
        resolve(root, rel_path)  # validate before touching the mirror
        for overlay_path in overlay:
            resolve(root, overlay_path)
        mirror = mirror_root(user)
        lock = render_locks.setdefault(user or "default", asyncio.Lock())

        async with lock:
            # mirror the templates dir so relative json()/image()/include paths resolve as they do on disk,
            # then overwrite just the buffers being edited so unsaved changes are what gets compiled
            sync_tree(root, mirror)
            typ_path = resolve(mirror, rel_path)
            for overlay_path, overlay_content in overlay.items():
                if overlay_content is not None:
                    write_text(resolve(mirror, overlay_path), overlay_content)
            if not os.path.exists(typ_path):
                raise Exception(f"'{rel_path}' not found")

            args = [TYPST, "compile", "--root", mirror, typ_path, "-", "-f", "pdf", "--diagnostic-format", "short"]
            fonts_dir = os.path.join(root, "fonts")
            if os.path.isdir(fonts_dir):
                args += ["--font-path", fonts_dir]

            ctx.dbg(f"Rendering: {' '.join(args)}")
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None, [{"severity": "error", "message": f"typst compile timed out after {RENDER_TIMEOUT}s"}]

        diagnostics = parse_diagnostics(stderr.decode("utf-8", errors="replace"), mirror)
        if proc.returncode != 0 or not stdout:
            if not any(d["severity"] == "error" for d in diagnostics):
                diagnostics.append({"severity": "error", "message": "typst compile failed"})
            return None, diagnostics
        return stdout, diagnostics

    def compile_error(diagnostics):
        message = next((d["message"] for d in diagnostics if d["severity"] == "error"), "typst compile failed")
        return {"responseStatus": {"errorCode": "CompileError", "message": message}, "diagnostics": diagnostics}

    async def render(request):
        """Compile a (possibly unsaved) template to PDF and return the raw bytes"""
        user = ctx.assert_username(request)
        body = await request.json()
        # unsaved buffers to compile with, keyed by their path relative to the templates folder
        pdf_bytes, diagnostics = await compile_pdf(user, body.get("path"), body.get("files") or {})
        if pdf_bytes is None:
            return web.json_response(compile_error(diagnostics), status=400)

        headers = {"Cache-Control": "no-store"}
        warnings = [d for d in diagnostics if d["severity"] == "warning"]
        if warnings:
            headers["X-Typst-Warnings"] = json.dumps(warnings)
        return web.Response(body=pdf_bytes, content_type="application/pdf", headers=headers)

    ctx.add_post("render", render)

    # AI template editing ---------------------------------------------------

    def find_model(model_id: str) -> Optional[dict]:
        for provider in ctx.get_providers().values():
            models = getattr(provider, "models", None) or {}
            for model in models.values() if isinstance(models, dict) else models:
                if isinstance(model, dict) and model_id in (model.get("id"), model.get("name")):
                    return model
        return None

    def assert_image_model(model_id: str):
        """Attachments are sent as images, so the model has to be able to see them"""
        model = find_model(model_id)
        if not model:
            return  # unknown to us (custom/proxied model), let the provider decide
        inputs = (model.get("modalities") or {}).get("input")
        if inputs and "image" not in inputs:
            raise Exception(
                f"'{model_id}' accepts {'/'.join(inputs)}, not images. Select a vision model to use attachments."
            )

    def read_attachments(body) -> list:
        """Data URLs for the screenshots (and rasterised PDF pages) the user attached"""
        images = body.get("images") or []
        if not isinstance(images, list):
            raise Exception("'images' must be a list of data URLs")
        if len(images) > MAX_IMAGES:
            raise Exception(f"Too many attachments: {len(images)}, at most {MAX_IMAGES}")
        total = 0
        for image in images:
            if not isinstance(image, str) or not IMAGE_DATA_URL_RE.match(image):
                raise Exception("Attachments must be image data URLs")
            total += len(image)
        if total > MAX_IMAGE_BYTES:
            raise Exception(f"Attachments are too large: {total // 1024}KB, at most {MAX_IMAGE_BYTES // 1024}KB")
        return images

    def assert_text_model(model_id: str):
        """The edit prompt needs a model that answers with text, not an image/audio generation model"""
        model = find_model(model_id)
        if not model:
            return  # unknown to us (custom/proxied model), let the provider decide
        output = (model.get("modalities") or {}).get("output")
        if output and "text" not in output:
            raise Exception(
                f"'{model_id}' outputs {'/'.join(output)}, not text. Select a text model to edit templates."
            )

    async def list_fonts(request):
        """Families `typst fonts` can see, including anything in the user's own fonts/ folder"""
        user = ctx.assert_username(request)
        font_dir = os.path.join(pdf_root(user), "fonts")
        has_dir = os.path.isdir(font_dir)
        # keyed on the folder's mtime so a font dropped in there shows up without a restart
        key = (font_dir, os.path.getmtime(font_dir)) if has_dir else None
        if key not in _fonts_cache:
            args = [TYPST, "fonts"] + (["--font-path", font_dir] if has_dir else [])
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
            names = {line.strip() for line in out.decode("utf-8", "replace").splitlines() if line.strip()}
            _fonts_cache[key] = sorted(names, key=str.casefold)
        return web.json_response({"fonts": _fonts_cache[key]})

    ctx.add_get("fonts", list_fonts)

    async def ai_edit(request):
        """Ask the selected model to rewrite the template (and its data files) to satisfy a request"""
        user = ctx.assert_username(request)
        root = pdf_root(user)
        body = await request.json()
        rel_path = body.get("path")
        prompt = (body.get("prompt") or "").strip()
        model = body.get("model")
        files = body.get("files") or {}
        images = read_attachments(body)

        if not prompt:
            raise Exception("Missing 'prompt'")
        if not model:
            raise Exception("No model selected")
        resolve(root, rel_path)
        assert_text_model(model)
        if images:
            assert_image_model(model)

        system_prompt = read_text(os.path.join(PROMPTS_DIR, "edit-template.md"))

        parts = [f"Template: `{rel_path}`", ""]
        for file_path, content in files.items():
            resolve(root, file_path)
            lang = "typst" if file_path.endswith(".typ") else os.path.splitext(file_path)[1].lstrip(".")
            parts.append(f"```{lang} path={file_path}\n{content}\n```")
            parts.append("")
        if images:
            noun = "screenshot" if len(images) == 1 else "screenshots/pages"
            parts.append(
                f"The user attached {len(images)} {noun} of the document they want. Reproduce that layout in "
                "the template and put any data you can read from it in the data file."
            )
            parts.append("")
        parts.append(f"Requested change:\n{prompt}")

        text = "\n".join(parts)
        # OpenAI style content parts - main.py normalises these for every provider
        content = (
            [{"type": "text", "text": text}]
            + [{"type": "image_url", "image_url": {"url": image}} for image in images]
            if images
            else text
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]
        chat_context = {"tools": "none", "nohistory": True, "nostore": True, "user": user}

        async def ask():
            response = await ctx.chat_completion({"model": model, "messages": messages}, context=chat_context)
            answer = (response.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            if not answer.strip():
                raise Exception("The model returned an empty response")
            edits = {}
            for edit_path, content in parse_edited_files(answer, rel_path, list(files)).items():
                resolve(root, edit_path)  # never let the model write outside the templates folder
                edits[edit_path] = content
            return answer, edits, response.get("usage")

        answer, edits, usage = await ask()

        # compile what came back and, if typst rejects it, give the model one shot at fixing its own output
        diagnostics = []
        repaired = False
        if edits:
            pdf_bytes, diagnostics = await compile_pdf(user, rel_path, {**files, **edits})
            if pdf_bytes is None:
                errors = "\n".join(
                    f"{d.get('file', rel_path)}:{d.get('line', '?')}:{d.get('col', '?')}: {d['message']}"
                    for d in diagnostics
                    if d["severity"] == "error"
                )
                ctx.log(f"AI edit failed to compile, asking {model} to fix:\n{errors}")
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": "That does not compile. typst reports:\n\n"
                        f"{errors}\n\n"
                        "Remember: inside a function's argument list you are already in code, so no `#` prefix, "
                        "and content is passed positionally. Fix it and return the complete corrected files in "
                        "`path=` tagged code blocks as before.",
                    }
                )
                retry_answer, retry_edits, retry_usage = await ask()
                if retry_edits:
                    merged = {**edits, **retry_edits}
                    pdf_bytes, retry_diagnostics = await compile_pdf(user, rel_path, {**files, **merged})
                    if pdf_bytes is not None or len(retry_diagnostics) < len(diagnostics):
                        answer, edits, diagnostics, repaired = retry_answer, merged, retry_diagnostics, True
                        usage = retry_usage

        return web.json_response(
            {
                "files": edits,
                "message": strip_code_blocks(answer),
                "model": model,
                "usage": usage,
                "repaired": repaired,
                "diagnostics": [d for d in diagnostics if d["severity"] == "error"],
            }
        )

    ctx.add_post("ai", ai_edit)


__install__ = install
