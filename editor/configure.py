#!/usr/bin/env python3
"""Regenerate tracked sources from config.json, or check them in CI."""
import argparse
import json
import sys
import model

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
try:
    config = model.validate(json.loads((model.ROOT / model.CONFIG).read_text()))
    files = model.generated(config)
    stale = []
    for name, contents in files.items():
        path = model.ROOT / name
        if args.check:
            if not path.exists() or path.read_text() != contents:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
    if stale:
        sys.exit("Generated sources differ from editor/config.json: " + ", ".join(stale))
    print("62-key profile valid; all 186 layer bindings and generated sources are consistent.")
except (model.Invalid, ValueError) as error:
    sys.exit(str(error))
