"""Read-only workspace browsing for the right sidebar."""
import asyncio
import base64
import os

from aiohttp import web

TEXT_PREVIEW_BYTES = 1024 * 1024
IMAGE_PREVIEW_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
    '.avif': 'image/avif', '.ico': 'image/x-icon', '.svg': 'image/svg+xml',
}


def within(path, root):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:
        return False


def resolve_directory(roots, path=None):
    """Validate a selected directory against the authenticated workspace roots."""
    roots = [os.path.realpath(p) for p in roots]
    if not roots:
        return roots, None, None
    path = os.path.realpath(path or roots[0])
    root = next((r for r in roots if within(path, r)), None)
    if root is None:
        raise web.HTTPForbidden(text='Path is outside the workspace')
    if not os.path.isdir(path):
        raise web.HTTPNotFound(text='Directory is unavailable')
    return roots, path, root


def browse(roots, path=None, file=None):
    roots, path, root = resolve_directory(roots, path)
    if root is None:
        return {'roots': [], 'entries': []}
    result = {'roots': roots, 'path': path, 'parent': os.path.dirname(path) if path != root else None}
    if file:
        target = os.path.realpath(file)
        if not within(target, root) or os.path.dirname(target) != path:
            raise web.HTTPForbidden(text='File is outside the selected directory')
        if not os.path.isfile(target):
            raise web.HTTPNotFound(text='File is unavailable')
        mime_type = IMAGE_TYPES.get(os.path.splitext(target)[1].lower())
        raster_image = mime_type and mime_type != 'image/svg+xml'
        limit = IMAGE_PREVIEW_BYTES if raster_image else TEXT_PREVIEW_BYTES
        with open(target, 'rb') as stream:
            content = stream.read(limit + 1)
        preview = result['file'] = {'path': target, 'name': os.path.basename(target)}
        if len(content) > limit:
            preview['message'] = f'File is too large to preview ({limit // TEXT_PREVIEW_BYTES} MiB limit).'
        elif not raster_image and b'\0' in content:
            preview['message'] = 'Binary file preview is unavailable.'
        else:
            if mime_type:
                # The same scoped/authenticated request supplies image bytes. SVG stays
                # an isolated image resource, never inline document markup in the UI.
                preview['mimeType'] = mime_type
                preview['image'] = f'data:{mime_type};base64,' + base64.b64encode(content).decode('ascii')
            if not raster_image:
                preview['content'] = content.decode('utf-8', errors='replace')
    entries = []
    with os.scandir(path) as scan:
        for item in scan:
            if not within(os.path.realpath(item.path), root):
                continue
            entries.append({'name': item.name, 'path': item.path, 'directory': item.is_dir()})
            if len(entries) >= 2000:
                result['truncated'] = True
                break
    result['entries'] = sorted(entries, key=lambda e: (not e['directory'], e['name'].casefold()))
    return result


def install_explorer(ctx):
    async def explorer(request):
        user = ctx.get_username(request)
        project_id = request.query.get('projectId') or None
        try:
            # The explorer has its own startup defaults; legacy active-project
            # permissions and durable chat workspace policy remain independent.
            roots = ctx.projects.resolve_explorer_workspace(project_id, user, is_admin=ctx.is_admin(request))['directories']
            if request.query.get('view', 'files') != 'files':
                raise web.HTTPBadRequest(text='Unsupported explorer view')
            result = await asyncio.to_thread(browse, roots, request.query.get('path'), request.query.get('file'))
            return web.json_response(result)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except OSError as exc:
            raise web.HTTPBadRequest(text='Unable to read this directory') from exc
    ctx.add_get('explorer', explorer)
