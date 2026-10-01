from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .analyzer import summarize_document_features

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".conf", ".log", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat",
    ".cmd", ".sql", ".graphql", ".gql", ".env", ".properties", ".rst"
}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
EVIDENCE_EXTS = IMAGE_EXTS | {".pdf"}
SUPPORTED_EXTS = TEXT_EXTS | OFFICE_EXTS | EVIDENCE_EXTS
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build"}
MAX_TEXT_BYTES = 5 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(min(size, MAX_TEXT_BYTES))
        if size > MAX_TEXT_BYTES:
            f.seek(max(0, size - 256 * 1024))
            raw += b"\n\n[...middle truncated...]\n\n" + f.read(256 * 1024)
    for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    texts = []
    for el in root.iter():
        if el.tag.endswith("}t") and el.text:
            texts.append(el.text)
        elif el.tag.endswith("}p"):
            texts.append("\n")
    return " ".join(texts).replace(" \n ", "\n")


def _extract_pptx(path: Path) -> str:
    chunks = []
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        for name in names:
            root = ET.fromstring(z.read(name))
            slide = []
            for el in root.iter():
                if el.tag.endswith("}t") and el.text:
                    slide.append(el.text)
            if slide:
                chunks.append(" ".join(slide))
    return "\n\n".join(chunks)


def _extract_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root:
                parts = [el.text or "" for el in si.iter() if el.tag.endswith("}t")]
                shared.append("".join(parts))
        rows_out = []
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        for sheet in sheets:
            root = ET.fromstring(z.read(sheet))
            rows_out.append(f"[{Path(sheet).stem}]")
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                vals = []
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    typ = cell.attrib.get("t", "")
                    v = next((x for x in cell if x.tag.endswith("}v")), None)
                    if v is None or v.text is None:
                        continue
                    value = v.text
                    if typ == "s":
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            pass
                    vals.append(value)
                if vals:
                    rows_out.append(" | ".join(vals))
    return "\n".join(rows_out)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        pass
    exe = shutil.which("pdftotext")
    if exe:
        try:
            result = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.decode("utf-8", errors="replace")
        except Exception:
            pass
    return "[PDF evidence indexed by filename/path only. Install pypdf for local PDF text extraction.]"


def extract_content(path: Path) -> tuple[str, list[str]]:
    ext = path.suffix.lower()
    tags = []
    if ext in TEXT_EXTS:
        text = _read_text(path)
        if ext in {".html", ".htm"}:
            text = _strip_html(text)
        elif ext == ".json":
            try:
                obj = json.loads(text)
                text = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                pass
        elif ext in {".csv", ".tsv"}:
            try:
                delimiter = "\t" if ext == ".tsv" else ","
                reader = csv.reader(io.StringIO(text), delimiter=delimiter)
                text = "\n".join(" | ".join(row) for row in reader)
            except Exception:
                pass
        return text, tags
    if ext == ".docx":
        return _extract_docx(path), ["office"]
    if ext == ".pptx":
        return _extract_pptx(path), ["office"]
    if ext == ".xlsx":
        return _extract_xlsx(path), ["office"]
    if ext == ".pdf":
        return _extract_pdf(path), ["evidence", "pdf"]
    if ext in IMAGE_EXTS:
        return f"[Image evidence] {path.name}\nPath: {path}", ["evidence", "image"]
    return "", []


def infer_competition(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    parts = rel.parts
    if len(parts) <= 1:
        return root.name
    first = parts[0]
    generic = {"screenshots", "images", "notes", "logs", "exports", "prompts", "responses", "evidence", "files"}
    if first.lower() in generic and len(parts) > 2:
        return parts[1]
    return first


def import_archive(db, root_path: str) -> dict[str, Any]:
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Folder does not exist or is not a directory")

    report: dict[str, Any] = {
        "root_path": str(root),
        "files_seen": 0,
        "files_added": 0,
        "files_updated": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": 0,
        "error_samples": [],
    }

    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        current_path = Path(current)
        for file_name in files:
            report["files_seen"] += 1
            path = current_path / file_name
            ext = path.suffix.lower()
            if ext not in SUPPORTED_EXTS:
                report["skipped"] += 1
                continue
            try:
                stat = path.stat()
                digest = sha256_file(path)
                if db.sha_exists(digest, excluding_path=str(path)):
                    report["duplicates"] += 1
                content, tags = extract_content(path)
                if not content.strip():
                    content = f"[Indexed artifact] {path.name}\nPath: {path}"
                features = summarize_document_features(content)
                rel = str(path.relative_to(root))
                doc = {
                    "path": str(path),
                    "rel_path": rel,
                    "competition": infer_competition(root, path),
                    "file_name": path.name,
                    "ext": ext,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "sha256": digest,
                    "content": content,
                    "outcome": features["outcome"],
                    "techniques": features["techniques"],
                    "tags": tags,
                    "token_counts": features["token_counts"],
                    "metadata": features.get("metadata", {}),
                }
                _, state = db.upsert_document(doc)
                if state == "added":
                    report["files_added"] += 1
                elif state == "updated":
                    report["files_updated"] += 1
            except Exception as exc:
                report["errors"] += 1
                if len(report["error_samples"]) < 12:
                    report["error_samples"].append({"file": str(path), "error": str(exc)})
    db.add_import(report)
    return report