#!/usr/bin/env python

import json
import os
from pathlib import Path

_ROOT = Path("/home/mythz/src/ServiceStack/llms/llms/extensions")
extensions_path = Path("/home/mythz/.llms/extensions")


def get_extensions_path():
    return extensions_path


def get_extensions_dirs():
    extensions_path = get_extensions_path()
    os.makedirs(extensions_path, exist_ok=True)

    # allow overriding builtin extensions
    override_extensions = []
    if os.path.exists(extensions_path):
        override_extensions = os.listdir(extensions_path)

    ret = []

    if os.path.exists(_ROOT):
        for dir in os.listdir(_ROOT):
            if os.path.isdir(os.path.join(_ROOT, dir)):
                if dir in override_extensions:
                    continue
                ret.append(os.path.join(_ROOT, dir))

    if os.path.exists(extensions_path):
        for dir in os.listdir(extensions_path):
            if os.path.isdir(os.path.join(extensions_path, dir)):
                ret.append(os.path.join(extensions_path, dir))

    return ret


# print(os.listdir(extensions_path))

print(json.dumps(get_extensions_dirs(), indent=2))


for dir_path in get_extensions_dirs():
    dir_name = os.path.basename(dir_path)
    print(f"{dir_name:<20} {dir_path}")
