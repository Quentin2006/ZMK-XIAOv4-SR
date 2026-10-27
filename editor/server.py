#!/usr/bin/env python3
"""Local-only Skree editor. No tokens leave the Git credential helper."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import model

REPOSITORY = "Quentin2006/ZMK-XIAOv4-SR"
BRANCH = "skree-control-panel"
REPO_URL = "https://github.com/" + REPOSITORY


class Conflict(model.Invalid):
    pass


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".skree-tmp")
    temporary.write_text(content)
    os.replace(temporary, path)


class Workspace:
    def __init__(self, root=model.ROOT):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.csrf = secrets.token_urlsafe(32)

    def git(self, *args, timeout=30):
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if result.returncode:
            raise model.Invalid((result.stderr or result.stdout or "Git command failed.").strip()[-1600:])
        return result.stdout.strip()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def revision(self):
        return self.head() + ":" + model.checksum(self.root)

    def assert_revision(self, expected):
        if expected != self.revision():
            raise Conflict("The repository changed since this draft was loaded. Refresh before saving; your browser draft is retained.")

    def assert_target(self):
        model.require(self.git("branch", "--show-current") == BRANCH, "Switch this checkout back to the editor branch before writing.")
        remote = self.git("remote", "get-url", "origin").removesuffix(".git")
        model.require(remote in {REPO_URL, "git@github.com:" + REPOSITORY}, "The GitHub remote differs from the configured keyboard repository.")

    def history(self):
        raw = self.git("log", "-15", "--format=%H%x1f%aI%x1f%s", "--", model.CONFIG)
        return [{"sha": parts[0], "date": parts[1], "message": parts[2], "url": REPO_URL + "/commit/" + parts[0]} for line in raw.splitlines() if len(parts := line.split("\x1f", 2)) == 3]

    def pending_commits(self):
        try:
            remote = "refs/remotes/origin/" + BRANCH
            self.git("rev-parse", "--verify", remote)
            return int(self.git("rev-list", "--count", remote + "..HEAD"))
        except model.Invalid:
            return None  # This branch has not been published or fetched yet.

    def state(self):
        config = model.validate(json.loads((self.root / model.CONFIG).read_text()))
        status = self.git("status", "--porcelain")
        drift = [p for p, body in model.generated(config, self.root).items() if not (self.root / p).exists() or (self.root / p).read_text() != body]
        applied = False
        if self.root == model.ROOT:
            module = Path.home() / ".config/hypr/skree-control.lua"
            inputs = Path.home() / ".config/hypr/input.lua"
            xkb = Path.home() / ".config/hypr/keymaps/skree-swapped-slashes.xkb"
            applied = module.exists() and module.read_text() == model.render_host(config) and inputs.exists() and 'require("hypr.skree-control")' in inputs.read_text() and xkb.exists() and xkb.read_bytes() == (self.root / model.KEYMAP_XKB).read_bytes()
        pending = self.pending_commits()
        return {"config": config, "revision": self.revision(), "head": self.head(), "branch": self.git("branch", "--show-current"), "repository": REPOSITORY, "repositoryUrl": REPO_URL, "branchUrl": REPO_URL + "/tree/" + BRANCH, "dirty": bool(status), "sourceDrift": drift, "status": status, "history": self.history(), "desktopApplied": applied, "pendingCommits": pending, "pendingPush": pending is None or pending > 0, "csrfToken": self.csrf}

    def save(self, payload):
        with self.lock:
            self.assert_target()
            self.assert_revision(payload.get("revision"))
            model.require(not self.git("status", "--porcelain"), "The checkout has other uncommitted edits. Commit or review them outside the editor before saving.")
            model.require(not self.state()["sourceDrift"], "Generated sources differ from editor/config.json. Reconcile them before saving; manual edits will not be overwritten.")
            config = model.validate(copy.deepcopy(payload.get("config")))
            message = payload.get("message", "")
            model.require(isinstance(message, str) and 3 <= len(message.strip()) <= 180 and not re.search(r"[\x00-\x1f]", message), "Write a commit message of 3–180 printable characters.")
            files = model.generated(config, self.root)
            model.require(any(not (self.root / p).exists() or (self.root / p).read_text() != body for p, body in files.items()), "There are no changes to save.")
            original = {p: (self.root / p).read_text() if (self.root / p).exists() else None for p in files}
            old_head = self.head()
            try:
                for path, body in files.items():
                    atomic_write(self.root / path, body)
                self.git("add", "--", *files)
                self.git("commit", "-m", message.strip())
            except Exception:
                if self.head() == old_head:
                    self.git("reset", "--", *files)
                    for path, body in original.items():
                        if body is None:
                            (self.root / path).unlink(missing_ok=True)
                        else:
                            atomic_write(self.root / path, body)
                raise
            committed = self.head()
            # A push failure never discards the local commit or claims it reached GitHub.
            try:
                self.git("push", "-u", "origin", BRANCH, timeout=60)
                pushed, error = True, None
            except (model.Invalid, subprocess.TimeoutExpired) as exc:
                pushed, error = False, str(exc)
            return {"state": self.state(), "commit": committed, "url": REPO_URL + "/commit/" + committed, "pushed": pushed, "pushError": error}

    def push(self, payload):
        with self.lock:
            self.assert_target()
            self.assert_revision(payload.get("revision"))
            self.git("push", "-u", "origin", BRANCH, timeout=60)
            return {"state": self.state(), "pushed": True}

    def refresh(self, payload):
        with self.lock:
            self.assert_target()
            self.assert_revision(payload.get("revision"))
            model.require(not self.git("status", "--porcelain"), "Refresh would overlap local file edits. Review the checkout first.")
            self.git("fetch", "origin", BRANCH, timeout=60)
            self.git("merge", "--ff-only", "origin/" + BRANCH)
            return {"state": self.state()}

    def restore(self, payload):
        sha = payload.get("sha", "")
        model.require(sha in {x["sha"] for x in self.history()}, "Choose a revision from this configuration’s history.")
        config = model.validate(json.loads(self.git("show", sha + ":" + model.CONFIG)))
        return {"config": config, "sha": sha}

    def apply(self, payload):
        with self.lock:
            self.assert_target()
            self.assert_revision(payload.get("revision"))
            model.require(self.root == model.ROOT, "Desktop apply is unavailable in a test workspace.")
            model.require(not self.git("status", "--porcelain"), "Save your configuration before applying desktop settings.")
            model.require(not self.state()["sourceDrift"], "Generated sources differ from the saved configuration; review them before applying.")
            config = model.validate(json.loads((self.root / model.CONFIG).read_text()))
            hypr = Path.home() / ".config/hypr"
            inputs, module = hypr / "input.lua", hypr / "skree-control.lua"
            xkb = hypr / "keymaps/skree-swapped-slashes.xkb"
            original = {p: p.read_bytes() if p.exists() else None for p in [inputs, module, xkb]}
            text = inputs.read_text()
            # Migrate only the two exact blocks previously created in this chat.
            if 'require("hypr.skree-control")' not in text:
                text, count1 = re.subn(r'-- Skree workaround:.*?hl\.device\(\{ name = "zmk-project-skree-custom-keyboard", kb_file = skree_slash_keymap \}\)\n', '', text, flags=re.S)
                text, count2 = re.subn(r'-- Skree trackball:.*?\nend\n', '', text, flags=re.S)
                model.require(count1 <= 1 and count2 <= 1, "Cannot safely migrate existing Skree settings.")
                model.require("skree_slash_keymap" not in text and 'name = "skree-custom-mouse"' not in text, "Custom Skree overrides need review before desktop apply.")
                text += '\n-- Version-controlled Skree settings managed by the local editor.\nrequire("hypr.skree-control")\n'
            else:
                model.require(text.count('require("hypr.skree-control")') == 1, "Duplicate managed profile imports need review.")
            backup_dir = self.root / ".local-state/desktop-backups"
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = str(__import__('time').time_ns())
            for p, data in original.items():
                if data is not None:
                    dest = backup_dir / (stamp + "-" + p.name)
                    dest.write_bytes(data)
                    dest.chmod(0o600)
            try:
                xkb.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / model.KEYMAP_XKB, xkb)
                checked = subprocess.run(["xkbcli", "compile-keymap", "--keymap", str(xkb), "--test"], capture_output=True, text=True, timeout=10)
                model.require(checked.returncode == 0, "The desktop keymap did not compile.")
                atomic_write(module, model.render_host(config))
                atomic_write(inputs, text)
                reload = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True, timeout=10)
                errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True, text=True, timeout=10)
                model.require(reload.returncode == 0 and errors.returncode == 0 and not errors.stdout.strip(), "Hyprland rejected the profile: " + errors.stdout.strip())
            except Exception:
                for path, data in original.items():
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(data)
                subprocess.run(["hyprctl", "reload"], capture_output=True, timeout=10)
                raise
            return {"state": self.state(), "applied": True, "message": "Saved desktop settings applied. Firmware was not flashed."}


class Handler(BaseHTTPRequestHandler):
    workspace = None
    protocol_version = "HTTP/1.1"

    def valid_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def respond(self, body, status=200, kind="application/json; charset=utf-8"):
        data = json.dumps(body).encode() if kind.startswith("application/json") else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.valid_host():
            return self.respond({"error": "Invalid local host."}, 403)
        try:
            path = urlsplit(self.path).path
            if path == "/api/state":
                return self.respond(self.workspace.state())
            if path == "/api/catalog":
                return self.respond({"keycodes": sorted(model.KEY_CODES), "mouse": sorted(model.MOUSE_CODES), "rgb": sorted(model.RGB_CODES)})
            allowed = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/geometry.json": ("geometry.json", "application/json; charset=utf-8")}
            if path not in allowed:
                return self.respond({"error": "Not found."}, 404)
            file, kind = allowed[path]
            data = (self.workspace.root / "editor/static" / file).read_bytes()
            if kind.startswith("application/json"):
                return self.respond(json.loads(data))
            self.respond(data, kind=kind)
        except Exception as error:
            self.respond({"error": str(error)}, 500)

    def do_POST(self):
        origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.valid_host() or self.headers.get("Origin") not in origins or self.headers.get("X-Skree-CSRF") != self.workspace.csrf:
            return self.respond({"error": "Open the local editor and retry this action."}, 403)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond({"error": "JSON is required."}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            model.require(0 < length <= 131072, "Request is too large or empty.")
            payload = json.loads(self.rfile.read(length))
            model.require(isinstance(payload, dict), "Invalid request.")
            route = urlsplit(self.path).path
            actions = {"/api/save": self.workspace.save, "/api/push": self.workspace.push, "/api/refresh": self.workspace.refresh, "/api/restore": self.workspace.restore, "/api/apply": self.workspace.apply}
            if route == "/api/preview":
                self.workspace.assert_revision(payload.get("revision"))
                return self.respond(model.preview(payload.get("config"), self.workspace.root))
            if route not in actions:
                return self.respond({"error": "Not found."}, 404)
            self.respond(actions[route](payload))
        except Conflict as error:
            self.respond({"error": str(error)}, 409)
        except (model.Invalid, json.JSONDecodeError, TypeError, KeyError) as error:
            self.respond({"error": str(error)}, 400)
        except subprocess.TimeoutExpired:
            self.respond({"error": "The operation timed out. Refresh the repository state before retrying; a commit or push may have completed."}, 504)
        except Exception as error:
            self.respond({"error": str(error)}, 500)


def main():
    parser = argparse.ArgumentParser(description="Open the local Skree keyboard control panel.")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    model.require(1024 <= args.port <= 65535, "Choose a non-privileged local port.")
    Handler.workspace = Workspace()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Skree Control is ready at http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
