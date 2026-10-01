from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL,
    competition TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL,
    ext TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'unknown',
    techniques TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    token_counts TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_documents_competition ON documents(competition);
CREATE INDEX IF NOT EXISTS idx_documents_outcome ON documents(outcome);
CREATE INDEX IF NOT EXISTS idx_documents_ext ON documents(ext);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path TEXT NOT NULL,
    files_seen INTEGER NOT NULL DEFAULT 0,
    files_added INTEGER NOT NULL DEFAULT 0,
    files_updated INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    competition TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER,
    challenge_name TEXT NOT NULL DEFAULT '',
    competition TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    response TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'unknown',
    technique TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(challenge_id) REFERENCES challenges(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_outcome ON attempts(outcome);
CREATE INDEX IF NOT EXISTS idx_attempts_technique ON attempts(technique);
CREATE INDEX IF NOT EXISTS idx_attempts_challenge ON attempts(challenge_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(content, path, competition, techniques)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', '1')"
                )
            except sqlite3.OperationalError:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', '0')"
                )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "metadata_json" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")

    def fts_enabled(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
            return bool(row and row[0] == "1")

    def upsert_document(self, doc: dict[str, Any]) -> tuple[int, str]:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, sha256 FROM documents WHERE path=?", (doc["path"],)
            ).fetchone()
            if existing and existing["sha256"] == doc["sha256"]:
                return int(existing["id"]), "unchanged"

            params = (
                doc["path"],
                doc["rel_path"],
                doc.get("competition", ""),
                doc["file_name"],
                doc.get("ext", ""),
                int(doc.get("size", 0)),
                float(doc.get("mtime", 0)),
                doc["sha256"],
                doc.get("content", ""),
                doc.get("outcome", "unknown"),
                json.dumps(doc.get("techniques", []), ensure_ascii=False),
                json.dumps(doc.get("tags", []), ensure_ascii=False),
                json.dumps(doc.get("token_counts", {}), ensure_ascii=False),
                json.dumps(doc.get("metadata", {}), ensure_ascii=False),
            )
            if existing:
                doc_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE documents SET
                        rel_path=?, competition=?, file_name=?, ext=?, size=?, mtime=?, sha256=?,
                        content=?, outcome=?, techniques=?, tags=?, token_counts=?, metadata_json=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        doc["rel_path"],
                        doc.get("competition", ""),
                        doc["file_name"],
                        doc.get("ext", ""),
                        int(doc.get("size", 0)),
                        float(doc.get("mtime", 0)),
                        doc["sha256"],
                        doc.get("content", ""),
                        doc.get("outcome", "unknown"),
                        json.dumps(doc.get("techniques", []), ensure_ascii=False),
                        json.dumps(doc.get("tags", []), ensure_ascii=False),
                        json.dumps(doc.get("token_counts", {}), ensure_ascii=False),
                        json.dumps(doc.get("metadata", {}), ensure_ascii=False),
                        doc_id,
                    ),
                )
                state = "updated"
            else:
                cur = conn.execute(
                    """
                    INSERT INTO documents(
                        path, rel_path, competition, file_name, ext, size, mtime, sha256,
                        content, outcome, techniques, tags, token_counts, metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    params,
                )
                doc_id = int(cur.lastrowid)
                state = "added"

            if self._fts_enabled_conn(conn):
                conn.execute("DELETE FROM document_fts WHERE rowid=?", (doc_id,))
                conn.execute(
                    "INSERT INTO document_fts(rowid, content, path, competition, techniques) VALUES(?,?,?,?,?)",
                    (
                        doc_id,
                        doc.get("content", ""),
                        doc["path"],
                        doc.get("competition", ""),
                        " ".join(doc.get("techniques", [])),
                    ),
                )
            return doc_id, state

    def _fts_enabled_conn(self, conn: sqlite3.Connection) -> bool:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
            return bool(row and row[0] == "1")
        except sqlite3.OperationalError:
            return False

    def sha_exists(self, sha256: str, excluding_path: str | None = None) -> bool:
        with self.connect() as conn:
            if excluding_path:
                row = conn.execute(
                    "SELECT 1 FROM documents WHERE sha256=? AND path<>? LIMIT 1",
                    (sha256, excluding_path),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM documents WHERE sha256=? LIMIT 1", (sha256,)
                ).fetchone()
            return row is not None

    def add_import(self, report: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO imports(root_path, files_seen, files_added, files_updated, duplicates, skipped, errors)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    report["root_path"],
                    report.get("files_seen", 0),
                    report.get("files_added", 0),
                    report.get("files_updated", 0),
                    report.get("duplicates", 0),
                    report.get("skipped", 0),
                    report.get("errors", 0),
                ),
            )
            return int(cur.lastrowid)

    def add_challenge(self, name: str, competition: str, objective: str, analysis: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO challenges(name, competition, objective, analysis_json) VALUES(?,?,?,?)",
                (name, competition, objective, json.dumps(analysis, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def add_attempt(self, payload: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO attempts(challenge_id, challenge_name, competition, prompt, response, outcome, technique, notes)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    payload.get("challenge_id"),
                    payload.get("challenge_name", ""),
                    payload.get("competition", ""),
                    payload.get("prompt", ""),
                    payload.get("response", ""),
                    payload.get("outcome", "unknown"),
                    payload.get("technique", ""),
                    payload.get("notes", ""),
                ),
            )
            return int(cur.lastrowid)

    def get_dashboard(self) -> dict[str, Any]:
        with self.connect() as conn:
            def scalar(sql: str, args: Iterable[Any] = ()) -> int:
                row = conn.execute(sql, tuple(args)).fetchone()
                return int(row[0] or 0)

            recent = [dict(r) for r in conn.execute(
                "SELECT id, root_path, files_added, files_updated, duplicates, errors, created_at FROM imports ORDER BY id DESC LIMIT 5"
            ).fetchall()]
            latest_challenges = [dict(r) for r in conn.execute(
                "SELECT id, name, competition, created_at FROM challenges ORDER BY id DESC LIMIT 5"
            ).fetchall()]
            return {
                "documents": scalar("SELECT COUNT(*) FROM documents"),
                "competitions": scalar("SELECT COUNT(DISTINCT competition) FROM documents WHERE competition<>''"),
                "challenges": scalar("SELECT COUNT(*) FROM challenges"),
                "attempts": scalar("SELECT COUNT(*) FROM attempts"),
                "successes": scalar("SELECT COUNT(*) FROM attempts WHERE outcome IN ('success','partial')"),
                "evidence": scalar("SELECT COUNT(*) FROM documents WHERE ext IN ('.png','.jpg','.jpeg','.webp','.gif','.pdf')"),
                "recent_imports": recent,
                "latest_challenges": latest_challenges,
            }

    def list_documents(self, limit: int = 100, offset: int = 0, ext_filter: list[str] | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if ext_filter:
                marks = ",".join("?" for _ in ext_filter)
                rows = conn.execute(
                    f"SELECT id,path,rel_path,competition,file_name,ext,size,mtime,outcome,techniques,tags,metadata_json,created_at FROM documents WHERE ext IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",
                    (*ext_filter, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,path,rel_path,competition,file_name,ext,size,mtime,outcome,techniques,tags,metadata_json,created_at FROM documents ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["techniques"] = json.loads(item["techniques"] or "[]")
                item["tags"] = json.loads(item["tags"] or "[]")
                try:
                    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                except json.JSONDecodeError:
                    item["metadata"] = {}
                out.append(item)
            return out

    def list_challenges(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id,name,competition,objective,analysis_json,created_at FROM challenges ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()]

    def list_attempts(self, challenge_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if challenge_id is None:
                rows = conn.execute(
                    "SELECT * FROM attempts ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM attempts WHERE challenge_id=? ORDER BY id DESC LIMIT ?",
                    (challenge_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def technique_stats(self) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, int]] = {}
        with self.connect() as conn:
            for row in conn.execute("SELECT techniques,outcome FROM documents"):
                try:
                    techs = json.loads(row["techniques"] or "[]")
                except json.JSONDecodeError:
                    techs = []
                for tech in techs:
                    item = counts.setdefault(tech, {"documents": 0, "success": 0, "failure": 0, "partial": 0})
                    item["documents"] += 1
                    if row["outcome"] in item:
                        item[row["outcome"]] += 1
            for row in conn.execute("SELECT technique,outcome FROM attempts WHERE technique<>''"):
                tech = row["technique"]
                item = counts.setdefault(tech, {"documents": 0, "success": 0, "failure": 0, "partial": 0})
                if row["outcome"] in item:
                    item[row["outcome"]] += 1
        result = []
        for tech, values in counts.items():
            total_outcomes = values["success"] + values["failure"] + values["partial"]
            weighted_success = values["success"] + values["partial"] * 0.5
            rate = round((weighted_success / total_outcomes) * 100, 1) if total_outcomes else None
            result.append({"technique": tech, **values, "success_rate": rate})
        result.sort(key=lambda x: ((x["success_rate"] or -1), x["documents"]), reverse=True)
        return result

    def competition_stats(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT competition, COUNT(*) AS documents,
                       SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END) AS failures,
                       MAX(mtime) AS latest_mtime
                FROM documents
                WHERE competition<>''
                GROUP BY competition
                ORDER BY documents DESC, competition ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def outcome_stats(self) -> dict[str, int]:
        with self.connect() as conn:
            result = {"success": 0, "failure": 0, "partial": 0, "unknown": 0}
            for row in conn.execute("SELECT outcome, COUNT(*) c FROM documents GROUP BY outcome"):
                result[row["outcome"] if row["outcome"] in result else "unknown"] += int(row["c"])
            for row in conn.execute("SELECT outcome, COUNT(*) c FROM attempts GROUP BY outcome"):
                result[row["outcome"] if row["outcome"] in result else "unknown"] += int(row["c"])
            return result

    def all_term_documents(self, ids: list[int] | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if ids:
                marks = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT id,path,competition,file_name,outcome,techniques,tags,token_counts,metadata_json,substr(content,1,2500) AS excerpt FROM documents WHERE id IN ({marks})",
                    tuple(ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,path,competition,file_name,outcome,techniques,tags,token_counts,metadata_json,substr(content,1,2500) AS excerpt FROM documents ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            out = []
            for r in rows:
                item = dict(r)
                for field, default in (("techniques", []), ("tags", []), ("token_counts", {}), ("metadata_json", {})):
                    try:
                        item[field] = json.loads(item[field] or json.dumps(default))
                    except json.JSONDecodeError:
                        item[field] = default
                item["metadata"] = item.pop("metadata_json", {})
                out.append(item)
            return out

    def fts_candidates(self, terms: list[str], limit: int = 120) -> list[int]:
        if not terms or not self.fts_enabled():
            return []
        query = " OR ".join(f'"{t}"' for t in terms[:18])
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT rowid FROM document_fts WHERE document_fts MATCH ? ORDER BY bm25(document_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
                return [int(r[0]) for r in rows]
            except sqlite3.OperationalError:
                return []


    def archive_tree(self, limit: int = 10000) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,path,rel_path,competition,file_name,ext,size,mtime,outcome,techniques,tags,metadata_json FROM documents ORDER BY rel_path COLLATE NOCASE LIMIT ?",
                (limit,),
            ).fetchall()

        root = {"name": "Archive", "path": "", "type": "folder", "children": {}, "files": []}

        def folder(parent: dict[str, Any], name: str, path: str) -> dict[str, Any]:
            child = parent["children"].get(name)
            if child is None:
                child = {"name": name, "path": path, "type": "folder", "children": {}, "files": []}
                parent["children"][name] = child
            return child

        for row in rows:
            item = dict(row)
            try:
                item["techniques"] = json.loads(item.get("techniques") or "[]")
            except json.JSONDecodeError:
                item["techniques"] = []
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except json.JSONDecodeError:
                item["tags"] = []
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}

            parts = list(Path(item["rel_path"]).parts)
            dirs = parts[:-1]
            node = root
            acc: list[str] = []
            for part in dirs:
                acc.append(part)
                node = folder(node, part, str(Path(*acc)))
            node["files"].append(item)

        def finalize(node: dict[str, Any]) -> dict[str, Any]:
            children = [finalize(v) for _, v in sorted(node["children"].items(), key=lambda kv: kv[0].lower())]
            files = sorted(node["files"], key=lambda x: x["file_name"].lower())
            outcome = {"success": 0, "partial": 0, "failure": 0, "unknown": 0}
            technique_counts: dict[str, int] = {}
            evidence = 0
            models: dict[str, int] = {}

            for f in files:
                key = f.get("outcome") if f.get("outcome") in outcome else "unknown"
                outcome[key] += 1
                if f.get("ext") in {".png",".jpg",".jpeg",".webp",".gif",".pdf"}:
                    evidence += 1
                for tech in f.get("techniques") or []:
                    technique_counts[tech] = technique_counts.get(tech, 0) + 1
                for model in (f.get("metadata") or {}).get("models", [])[:4]:
                    models[model] = models.get(model, 0) + 1

            for child in children:
                for k, v in child["outcomes"].items():
                    outcome[k] += v
                evidence += child["evidence"]
                for tech, count in child["technique_counts"].items():
                    technique_counts[tech] = technique_counts.get(tech, 0) + count
                for model, count in child["model_counts"].items():
                    models[model] = models.get(model, 0) + count

            node_out = {
                "name": node["name"],
                "path": node["path"],
                "type": "folder",
                "file_count": len(files) + sum(c["file_count"] for c in children),
                "direct_files": len(files),
                "folder_count": len(children),
                "evidence": evidence,
                "outcomes": outcome,
                "technique_counts": technique_counts,
                "model_counts": models,
                "top_techniques": [k for k, _ in sorted(technique_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
                "top_models": [k for k, _ in sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))[:4]],
                "children": children,
                "files": files,
            }
            return node_out

        tree = finalize(root)
        return {
            "tree": tree,
            "groups": tree["children"],
            "total_files": tree["file_count"],
            "truncated": len(rows) >= limit,
        }

    def technique_details(self, technique: str, limit: int = 250) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,path,rel_path,competition,file_name,ext,size,outcome,techniques,tags,metadata_json,updated_at FROM documents ORDER BY updated_at DESC LIMIT 10000"
            ).fetchall()
            attempts = conn.execute(
                "SELECT id,challenge_name,competition,prompt,response,outcome,technique,notes,created_at FROM attempts WHERE technique=? ORDER BY id DESC LIMIT ?",
                (technique, limit),
            ).fetchall()

        documents = []
        competitions: dict[str, dict[str, int]] = {}
        outcomes = {"success": 0, "partial": 0, "failure": 0, "unknown": 0}
        for row in rows:
            item = dict(row)
            try:
                techs = json.loads(item.get("techniques") or "[]")
            except json.JSONDecodeError:
                techs = []
            if technique not in techs:
                continue
            item["techniques"] = techs
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except json.JSONDecodeError:
                item["tags"] = []
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            documents.append(item)
            oc = item.get("outcome") if item.get("outcome") in outcomes else "unknown"
            outcomes[oc] += 1
            comp = item.get("competition") or "Unassigned"
            c = competitions.setdefault(comp, {"documents": 0, "success": 0, "partial": 0, "failure": 0, "unknown": 0})
            c["documents"] += 1
            c[oc] += 1
            if len(documents) >= limit:
                break

        attempts_out = [dict(r) for r in attempts]
        for item in attempts_out:
            oc = item.get("outcome") if item.get("outcome") in outcomes else "unknown"
            outcomes[oc] += 1

        rated = outcomes["success"] + outcomes["partial"] + outcomes["failure"]
        rate = round((outcomes["success"] + outcomes["partial"] * 0.5) / rated * 100, 1) if rated else None
        comp_rows = [{"competition": k, **v} for k, v in competitions.items()]
        comp_rows.sort(key=lambda x: (-x["documents"], x["competition"].lower()))
        return {
            "technique": technique,
            "documents": documents,
            "attempts": attempts_out,
            "competitions": comp_rows,
            "outcomes": outcomes,
            "success_rate": rate,
        }

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )