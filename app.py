from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from core.analyzer import TECHNIQUES, analyze_objective
from core.db import Database
from core.importer import EVIDENCE_EXTS, import_archive
from core.promptgen import generate_prompt_pack
from core.search import search_documents, similar_documents

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
DB = Database(DATA / "command_center.db")
HOST = "127.0.0.1"
PORT = int(os.environ.get("RTCC_PORT", "8765"))


def jdump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_json_bytes(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def folder_listing(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        if os.name == "nt":
            drives = []
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                p = Path(f"{letter}:\\")
                if p.exists():
                    drives.append({"name": str(p), "path": str(p)})
            return {"path": "", "parent": "", "folders": drives}
        home = Path.home()
        return {
            "path": str(home),
            "parent": str(home.parent),
            "folders": _subdirs(home),
        }
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("Folder does not exist")
    parent = path.parent if path.parent != path else path
    return {"path": str(path), "parent": str(parent), "folders": _subdirs(path)}


def _subdirs(path: Path) -> list[dict[str, str]]:
    out = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return out
    for item in entries:
        try:
            if item.is_dir() and not item.name.startswith("."):
                out.append({"name": item.name, "path": str(item)})
        except OSError:
            continue
        if len(out) >= 300:
            break
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "RTCC/1.3"

    def log_message(self, fmt: str, *args):
        if os.environ.get("RTCC_QUIET") != "1":
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200):
        self._send(status, jdump(payload))

    def _error(self, message: str, status: int = 400):
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 8 * 1024 * 1024:
            raise ValueError("Request too large")
        return parse_json_bytes(self.rfile.read(length))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._get_api(path, query)
            else:
                self._static(path)
        except ValueError as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            self._error(f"Internal error: {exc}", 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            self._post_api(parsed.path, self._body())
        except ValueError as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            self._error(f"Internal error: {exc}", 500)

    def _get_api(self, path: str, query: dict[str, list[str]]):
        if path == "/api/health":
            return self._json({"ok": True, "name": "AI Red Team Command Center", "version": "1.3.0", "external_ai": False})
        if path == "/api/dashboard":
            return self._json({"ok": True, "data": DB.get_dashboard()})
        if path == "/api/documents":
            limit = max(1, min(500, int(query.get("limit", ["100"])[0])))
            offset = max(0, int(query.get("offset", ["0"])[0]))
            return self._json({"ok": True, "data": DB.list_documents(limit=limit, offset=offset)})
        if path == "/api/archive/tree":
            return self._json({"ok": True, "data": DB.archive_tree()})
        if path == "/api/technique/detail":
            tech = query.get("id", [""])[0].strip()
            if not tech:
                raise ValueError("Technique id is required")
            return self._json({"ok": True, "data": DB.technique_details(tech)})
        if path == "/api/evidence":
            return self._json({"ok": True, "data": DB.list_documents(limit=300, ext_filter=sorted(EVIDENCE_EXTS))})
        if path == "/api/challenges":
            data = []
            for item in DB.list_challenges():
                try:
                    item["analysis"] = json.loads(item.pop("analysis_json"))
                except Exception:
                    item["analysis"] = {}
                data.append(item)
            return self._json({"ok": True, "data": data})
        if path == "/api/attempts":
            challenge_id = query.get("challenge_id", [None])[0]
            cid = int(challenge_id) if challenge_id else None
            return self._json({"ok": True, "data": DB.list_attempts(challenge_id=cid)})
        if path == "/api/techniques":
            stats = {x["technique"]: x for x in DB.technique_stats()}
            data = []
            for key, meta in TECHNIQUES.items():
                item = {"id": key, "label": meta["label"], "why": meta["why"], **stats.get(key, {})}
                data.append(item)
            data.sort(key=lambda x: ((x.get("success_rate") or -1), x.get("documents", 0)), reverse=True)
            return self._json({"ok": True, "data": data})
        if path == "/api/stats":
            return self._json({"ok": True, "data": {
                "techniques": DB.technique_stats(),
                "competitions": DB.competition_stats(),
                "outcomes": DB.outcome_stats(),
            }})
        if path == "/api/search":
            q = query.get("q", [""])[0].strip()
            if not q:
                return self._json({"ok": True, "data": []})
            return self._json({"ok": True, "data": search_documents(DB, q, limit=50)})
        if path == "/api/fs":
            return self._json({"ok": True, "data": folder_listing(query.get("path", [None])[0])})
        if path == "/api/settings":
            return self._json({"ok": True, "data": {"archive_root": DB.get_setting("archive_root", "")}})
        self._error("Unknown API endpoint", 404)

    def _post_api(self, path: str, body: dict[str, Any]):
        if path == "/api/import":
            folder = str(body.get("path", "")).strip()
            if not folder:
                raise ValueError("Folder path is required")
            report = import_archive(DB, folder)
            DB.set_setting("archive_root", folder)
            return self._json({"ok": True, "data": report})
        if path == "/api/analyze":
            text = str(body.get("text", "")).strip()
            if not text:
                raise ValueError("Competition objective is required")
            name = str(body.get("name", "Untitled challenge")).strip() or "Untitled challenge"
            competition = str(body.get("competition", "")).strip()
            analysis = analyze_objective(text, historical_stats=DB.technique_stats())
            matches = similar_documents(DB, text, limit=10)
            analysis["historical_matches"] = matches
            challenge_id = None
            if bool(body.get("save", True)):
                challenge_id = DB.add_challenge(name, competition, text, analysis)
            return self._json({"ok": True, "challenge_id": challenge_id, "data": analysis})
        if path == "/api/prompts":
            analysis = body.get("analysis")
            if not isinstance(analysis, dict):
                text = str(body.get("text", "")).strip()
                if not text:
                    raise ValueError("Analysis or objective text is required")
                analysis = analyze_objective(text, historical_stats=DB.technique_stats())
            direction = str(body.get("direction", "")).strip() or None
            count = max(1, min(20, int(body.get("count", 10))))
            mode = str(body.get("mode", "standard")).strip().lower()
            if mode not in {"standard", "classifier", "hybrid"}:
                mode = "standard"
            return self._json({"ok": True, "data": generate_prompt_pack(analysis, direction=direction, count=count, mode=mode)})
        if path == "/api/attempts":
            outcome = str(body.get("outcome", "unknown"))
            if outcome not in {"success", "failure", "partial", "unknown"}:
                raise ValueError("Invalid outcome")
            body["outcome"] = outcome
            attempt_id = DB.add_attempt(body)
            return self._json({"ok": True, "id": attempt_id})
        if path == "/api/settings":
            archive_root = str(body.get("archive_root", "")).strip()
            DB.set_setting("archive_root", archive_root)
            return self._json({"ok": True})
        self._error("Unknown API endpoint", 404)

    def _static(self, path: str):
        if path in {"", "/"}:
            target = WEB / "index.html"
        else:
            rel = urllib.parse.unquote(path.lstrip("/"))
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents and target != WEB.resolve():
                return self._error("Not found", 404)
            if not target.exists() or not target.is_file():
                target = WEB / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def run(open_browser: bool = True):
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    print(f"AI Red Team Command Center running at http://{HOST}:{PORT}")
    print("No external AI services are used.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run(open_browser=os.environ.get("RTCC_NO_BROWSER") != "1")