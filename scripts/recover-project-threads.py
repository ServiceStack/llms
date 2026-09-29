"""Recover legacy project membership from saved tool workspace reports.

Dry run by default; --apply backs up SQLite before changing membership only.
"""
import argparse
import collections
import datetime
import json
import pathlib
import sqlite3


def recover_project(messages, directories):
    matches = set()
    for message in messages:
        content = message.get('content')
        if message.get('role') != 'tool' or not isinstance(content, str):
            continue
        # Only use explicit sandbox reports, never prose or guessed title matches.
        marker = 'allowed directories:\n'
        offset = content.lower().find(marker)
        if offset < 0:
            continue
        for line in content[offset + len(marker):].splitlines():
            path = line.strip().replace('\\', '/').rstrip('/')
            if path in directories:
                matches.add(directories[path])
    return next(iter(matches)) if len(matches) == 1 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=pathlib.Path)
    parser.add_argument('--user', required=True)
    parser.add_argument('--projects', type=pathlib.Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    projects = json.loads(args.projects.read_text())
    directories = {
        str(args.projects.parent / p['folder']).replace('\\', '/').rstrip('/'): p['id']
        for p in projects if p.get('folder') and p.get('id')
    }
    names = {p['id']: p['name'] for p in projects if p.get('id')}
    uri = args.database.resolve().as_uri() + ('?mode=rw' if args.apply else '?mode=ro')
    with sqlite3.connect(uri, uri=True) as conn:
        changes = []
        for tid, raw in conn.execute(
            'SELECT id,messages FROM thread WHERE user=? AND projectId IS NULL '
            'AND COALESCE(membershipVersion,0)=0', (args.user,)
        ):
            try:
                messages = json.loads(raw or '[]')
                project = recover_project(messages, directories)
            except (ValueError, TypeError, AttributeError):
                continue
            if project:
                changes.append((project, tid))
        print(json.dumps({'threads': len(changes), 'projects': dict(collections.Counter(
            names[project] for project, _ in changes)), 'threadIds': [tid for _, tid in changes]}, indent=2))
        if args.apply and changes:
            backup = str(args.database) + '.before-project-recovery-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            with sqlite3.connect(backup) as dest:
                conn.backup(dest)
            print('Backup:', backup)
            conn.executemany(
                'UPDATE thread SET projectId=?, membershipVersion=COALESCE(membershipVersion,0)+1, '
                'metadataVersion=COALESCE(metadataVersion,0)+1 WHERE id=? AND projectId IS NULL '
                'AND COALESCE(membershipVersion,0)=0', changes)
            conn.commit()
            print('Applied membership recovery.')


if __name__ == '__main__':
    main()
