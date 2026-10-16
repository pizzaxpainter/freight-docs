"""Core engine: ingest -> convert -> classify -> split -> merge. No UI code in here.

Everything lives in a per-session temporary folder (no database). Nothing is kept after the
session is cleaned up or the retention period expires.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pypdf import PdfReader, PdfWriter

import settings as S
from classifier import classify, classify_pages

Msg = tuple[str, str]  # (level, text) where level is "error" | "warning" | "info"

_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


# ======================================================================================
# Small helpers
# ======================================================================================
def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def clean_name(name: str) -> str:
    """Last path component only, with characters that are unsafe in file names removed."""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    return name[:150] or "file"


def validate_hawb(raw: str) -> tuple[str, list[str], list[str]]:
    """Return (normalised HAWB, errors, warnings)."""
    h = re.sub(r"\s+", "", raw or "").upper()
    errors, warnings = [], []
    if not h:
        errors.append("Enter the HAWB number before generating the final PDFs.")
    elif not re.fullmatch(S.HAWB_ALLOWED, h):
        errors.append("The HAWB may only contain letters, digits, hyphen and underscore (4-30 characters).")
    elif not re.fullmatch(S.HAWB_TYPICAL, h):
        warnings.append("This HAWB does not look like the usual format (3 letters + 8 digits, e.g. SIN01234567). "
                        "Please double-check it.")
    return h, errors, warnings


def _run(cmd: list[str], timeout: int, env: dict | None = None) -> tuple[int, bytes, bytes]:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise
    return p.returncode, out, err


def extract_page_texts(pdf: Path, max_pages: int | None = None) -> list[str]:
    """Text of each page (poppler's pdftotext, falling back to pypdf)."""
    max_pages = max_pages or S.MAX_TEXT_PAGES
    exe = shutil.which("pdftotext")
    if exe:
        try:
            rc, out, _ = _run([exe, "-enc", "UTF-8", "-l", str(max_pages), str(pdf), "-"], 90)
            if rc == 0:
                pages = out.decode("utf-8", "replace").split("\f")
                if pages and pages[-1] == "":
                    pages.pop()
                return pages
        except Exception:
            pass
    try:
        r = PdfReader(str(pdf), strict=False)
        return [(p.extract_text() or "") for p in list(r.pages)[:max_pages]]
    except Exception:
        return []


def render_pages(pdf: Path, dpi: int, first: int, last: int) -> list[bytes]:
    """Render pages first..last (1-based, inclusive) to PNG bytes using poppler."""
    exe = shutil.which("pdftoppm")
    if not exe:
        raise RuntimeError("Page preview is unavailable on the server (poppler-utils missing).")
    with tempfile.TemporaryDirectory(prefix="fdp_prev_") as tmp:
        rc, _, err = _run([exe, "-png", "-r", str(dpi), "-f", str(first), "-l", str(last),
                           str(pdf), str(Path(tmp) / "p")], 120)
        if rc != 0:
            raise RuntimeError("The preview could not be rendered.")
        return [f.read_bytes() for f in sorted(Path(tmp).glob("p-*.png"))]


def _open_pdf(path: Path) -> PdfReader:
    r = PdfReader(str(path), strict=False)
    if r.is_encrypted and not r.decrypt(""):
        raise ValueError("The PDF is password-protected. Ask the sender for an unprotected copy.")
    if len(r.pages) == 0:
        raise ValueError("The PDF has no pages.")
    return r


def prepare_pdf(src: Path, dst: Path) -> tuple[int, list[str]]:
    """Validate (and if needed repair/decrypt) a PDF and store a clean copy at dst."""
    notes: list[str] = []
    reader, used = None, src
    try:
        reader = _open_pdf(src)
    except ValueError:
        raise
    except Exception:
        qpdf = shutil.which("qpdf")
        if qpdf:
            repaired = dst.with_name(dst.stem + "_repaired.pdf")
            try:
                _run([qpdf, str(src), str(repaired)], 120)
                if repaired.exists():
                    reader = _open_pdf(repaired)
                    used = repaired
                    notes.append("The PDF was damaged and has been repaired automatically - please check the preview.")
            except Exception:
                reader = None
        if reader is None:
            raise ValueError("The PDF is corrupted and cannot be read.")
    if reader.is_encrypted:  # opened with the empty (owner-only) password: store an unlocked copy
        w = PdfWriter()
        w.append(reader)
        with open(dst, "wb") as fh:
            w.write(fh)
    else:
        shutil.copyfile(used, dst)
    return len(reader.pages), notes


def compact_ranges(pages: list[int]) -> str:
    """[1,2,3,5,7,8] -> '1-3,5,7-8'"""
    out, start, prev = [], None, None
    for p in pages:
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = p
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(out)


# ======================================================================================
# Data classes
# ======================================================================================
@dataclass
class Doc:
    id: str
    original_name: str
    source: str
    ext: str
    orig_path: Path
    sha256: str
    display_name: str = ""
    pdf_path: Path | None = None
    pages: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    category: str | None = None
    confidence: str = "none"
    needs_review: bool = True
    reason: str = ""
    page_cats: list = field(default_factory=list)
    mixed: bool = False
    convert_flag: bool = False
    order: float = 0.0

    @property
    def status(self) -> str:
        if self.error:
            return "Conversion Failed"
        if self.pdf_path is None:
            return "Uploaded"
        if self.needs_review or self.category is None:
            return "Needs Review"
        return "PDF Ready"

    @property
    def format(self) -> str:
        return self.ext.lstrip(".").upper()


@dataclass
class Output:
    category: str
    filename: str
    path: Path
    pages: int
    size: int


# ======================================================================================
# Workspace = one operator session
# ======================================================================================
_last_purge = 0.0


class Workspace:
    def __init__(self, root: Path | None = None):
        root = Path(root or S.WORK_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="ws_", dir=root))
        for sub in ("orig", "pdf", "out", "home"):
            (self.dir / sub).mkdir()
        os.chmod(self.dir, 0o700)
        self.docs: list[Doc] = []
        self.log: list[Msg] = []
        self.seen_upload_ids: set = set()
        self.outputs: list[Output] = []
        self.gen_errors: list[str] = []
        self.generated_for: str = ""
        self._hashes: dict[str, str] = {}
        self._seq = 0
        self._extracted = 0

    # ---- housekeeping -------------------------------------------------------------
    def touch(self) -> None:
        try:
            os.utime(self.dir, None)
        except OSError:
            pass

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def purge_old(root: Path | None = None, max_age_s: int | None = None, min_interval_s: int = 60) -> int:
        """Delete session folders idle for longer than the retention period."""
        global _last_purge
        now = time.time()
        if now - _last_purge < min_interval_s:
            return 0
        _last_purge = now
        root = Path(root or S.WORK_ROOT)
        max_age_s = S.RETENTION_MINUTES * 60 if max_age_s is None else max_age_s
        removed = 0
        if root.is_dir():
            for d in root.iterdir():
                try:
                    if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                        shutil.rmtree(d, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass
        return removed

    def _dirty(self) -> None:
        """Any change to the documents invalidates previously generated outputs."""
        self.outputs, self.gen_errors, self.generated_for = [], [], ""
        self.touch()

    def _say(self, msgs: list[Msg], level: str, text: str) -> None:
        msgs.append((level, text))
        self.log.append((level, text))

    # ---- ingest -------------------------------------------------------------------
    def ingest(self, name: str, data: bytes) -> tuple[list[Doc], list[Msg]]:
        """Register an uploaded file (expanding ZIPs). Conversion happens later in process()."""
        msgs: list[Msg] = []
        new: list[Doc] = []
        base = clean_name(name)
        ext = Path(base).suffix.lower()
        if ext not in S.ALLOWED_EXTENSIONS:
            self._say(msgs, "warning", f"Ignored '{base}': unsupported file type ({ext or 'no extension'}). "
                                       "Accepted: ZIP, PDF, DOC, DOCX, XLS, XLSX.")
        elif len(data) > S.MAX_FILE_BYTES:
            self._say(msgs, "error", f"'{base}' is larger than the {S.MAX_FILE_BYTES // 1048576} MB limit and was not added.")
        elif ext == ".zip":
            self._ingest_zip(data, base, 0, new, msgs)
        else:
            self._add_member(base, f"Upload: {base}", data, new, msgs)
        if new:
            self._dirty()
        return new, msgs

    def _ingest_zip(self, data: bytes, zip_label: str, depth: int, new: list[Doc], msgs: list[Msg]) -> None:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, zipfile.LargeZipFile):
            self._say(msgs, "error", f"'{zip_label}' is not a valid ZIP file or it is corrupted. Nothing was extracted.")
            return
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > S.MAX_ZIP_ENTRIES:
            self._say(msgs, "error", f"'{zip_label}' contains too many files ({len(infos)}; limit {S.MAX_ZIP_ENTRIES}). Nothing was extracted.")
            return
        if any(i.flag_bits & 0x1 for i in infos):
            self._say(msgs, "error", f"'{zip_label}' is password-protected. Ask the sender for an unprotected ZIP or extract it yourself and upload the files.")
            return
        if sum(i.file_size for i in infos) > S.MAX_EXTRACTED_BYTES:
            self._say(msgs, "error", f"'{zip_label}' would expand to more than {S.MAX_EXTRACTED_BYTES // 1048576} MB and was rejected.")
            return
        found_any = False
        msgs_before = len(msgs)
        for info in infos:
            norm = info.filename.replace("\\", "/")
            parts = [p for p in norm.split("/") if p not in ("", ".")]
            if not parts:
                continue
            base = parts[-1]
            if (base.lower() in _JUNK_NAMES or base.startswith("._") or base.startswith("~$")
                    or "__MACOSX" in parts):
                continue  # operating-system / Office metadata - not a document
            if norm.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", norm):
                self._say(msgs, "warning", f"Skipped '{norm}' in '{zip_label}': unsafe file path.")
                continue
            label = f"{zip_label}/{'/'.join(parts)}"
            ext = Path(base).suffix.lower()
            if ext not in S.ALLOWED_EXTENSIONS:
                self._say(msgs, "warning", f"Ignored '{label}': unsupported file type ({ext or 'no extension'}).")
                continue
            if (info.compress_size and info.file_size > 10 * 1048576
                    and info.file_size / info.compress_size > S.MAX_COMPRESSION_RATIO):
                self._say(msgs, "error", f"Skipped '{label}': suspicious compression ratio.")
                continue
            try:
                with zf.open(info) as fh:
                    blob = fh.read(S.MAX_FILE_BYTES + 1)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError):
                self._say(msgs, "error", f"Could not extract '{label}': the entry is corrupted, encrypted or uses an unsupported compression.")
                continue
            if len(blob) > S.MAX_FILE_BYTES:
                self._say(msgs, "error", f"Skipped '{label}': larger than the {S.MAX_FILE_BYTES // 1048576} MB per-file limit.")
                continue
            self._extracted += len(blob)
            if self._extracted > S.MAX_EXTRACTED_BYTES:
                self._say(msgs, "error", "Extraction stopped: the session size limit was reached. Remove documents or start a new shipment.")
                return
            found_any = True
            if ext == ".zip":
                if depth + 1 >= S.MAX_ZIP_DEPTH:
                    self._say(msgs, "warning", f"Skipped nested ZIP '{label}': ZIPs are nested too deeply.")
                else:
                    self._ingest_zip(blob, label, depth + 1, new, msgs)
            else:
                self._add_member(base, label, blob, new, msgs)
        if not found_any and len(msgs) == msgs_before:
            self._say(msgs, "warning", f"'{zip_label}' contains no supported documents.")

    def _add_member(self, base: str, source: str, data: bytes, new: list[Doc], msgs: list[Msg]) -> None:
        if len(self.docs) + len(new) >= S.MAX_DOCS_PER_SESSION:
            self._say(msgs, "error", f"Document limit reached ({S.MAX_DOCS_PER_SESSION}); '{source}' was not added.")
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._hashes:
            self._say(msgs, "info", f"Skipped duplicate: '{source}' is identical to '{self._hashes[digest]}'.")
            return
        self._hashes[digest] = source
        ext = Path(base).suffix.lower()
        doc_id = uuid.uuid4().hex[:10]
        path = self.dir / "orig" / f"{doc_id}{ext}"
        path.write_bytes(data)
        self._seq += 1
        doc = Doc(id=doc_id, original_name=base, source=source, ext=ext, orig_path=path,
                  sha256=digest, display_name=Path(base).stem, order=float(self._seq))
        head = data[:1024]
        if ext == ".pdf":
            if b"%PDF" not in head:
                doc.error = "This file is not a valid PDF (wrong file type or corrupted)."
        elif ext in (".docx", ".xlsx"):
            if not head.startswith(b"PK"):
                doc.warnings.append("The file does not look like a genuine Office file; conversion may fail.")
        elif ext in (".doc", ".xls"):
            if not head.startswith(_OLE_MAGIC):
                doc.warnings.append("The file does not look like a genuine legacy Office file; conversion may be unreliable.")
        new.append(doc)
        self.docs.append(doc)

    # ---- processing (convert + classify) --------------------------------------------
    def process(self, doc: Doc) -> Doc:
        if doc.error:
            return doc
        try:
            dst = self.dir / "pdf" / f"{doc.id}.pdf"
            if doc.ext == ".pdf":
                doc.pages, notes = prepare_pdf(doc.orig_path, dst)
                doc.warnings += notes
            else:
                self._convert_office(doc, dst)
            doc.pdf_path = dst
            self._analyse(doc)
        except ValueError as e:
            doc.error = str(e)
            doc.pdf_path = None
        except subprocess.TimeoutExpired:
            doc.error = "Conversion timed out. The file may be too large or corrupted."
            doc.pdf_path = None
        except Exception as e:  # never crash the app on a single bad file
            doc.error = f"Unexpected error while processing this file ({type(e).__name__})."
            doc.pdf_path = None
        self.touch()
        return doc

    def _convert_office(self, doc: Doc, dst: Path) -> None:
        exe = shutil.which("soffice") or shutil.which("libreoffice")
        if not exe:
            raise ValueError("Office conversion is unavailable on the server (LibreOffice is not installed). Contact IT.")
        work = self.dir / "lo" / doc.id
        work.mkdir(parents=True, exist_ok=True)
        src = work / f"input{doc.ext}"
        shutil.copyfile(doc.orig_path, src)
        profile = (self.dir / "lo_profile").resolve()
        env = {**os.environ, "HOME": str(self.dir / "home")}
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", "pdf", "--outdir", str(work), str(src)]
        _run(cmd, S.CONVERT_TIMEOUT_S, env=env)
        out = work / "input.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise ValueError("The file could not be converted to PDF (it may be corrupted or password-protected).")
        doc.pages, notes = prepare_pdf(out, dst)
        doc.warnings += notes
        # --- plausibility checks: flag, never silently accept -----------------------------
        text = "".join(extract_page_texts(dst, 5)).strip()
        if not text:
            doc.convert_flag = True
            doc.warnings.append("The converted PDF contains no readable text. Compare the preview with the original.")
        if doc.pages > S.OFFICE_MANY_PAGES_WARNING:
            doc.convert_flag = True
            doc.warnings.append(f"The converted PDF has {doc.pages} pages (wide sheets may have been split). Check the preview.")
        if doc.ext == ".xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(doc.orig_path, read_only=True)
                hidden = [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]
                if hidden:
                    doc.warnings.append(f"Hidden worksheet(s) are not printed: {', '.join(hidden)}.")
                if len(wb.worksheets) > 1:
                    doc.warnings.append(f"The workbook has {len(wb.worksheets)} worksheets - confirm all expected sheets appear in the PDF.")
                wb.close()
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)

    def _analyse(self, doc: Doc) -> None:
        texts = extract_page_texts(doc.pdf_path)
        head = "\n".join(texts[: S.CLASSIFY_PAGES])
        if not head.strip():
            doc.category, doc.confidence, doc.needs_review = None, "none", True
            doc.reason = "No readable text found (scanned image?). OCR is not part of this release - please choose the category manually."
        else:
            res = classify(head, doc.original_name)
            doc.category, doc.confidence, doc.needs_review, doc.reason = (
                res.category, res.confidence, res.needs_review, res.reason)
        if doc.pages > 1 and texts:
            cats = classify_pages(texts)
            doc.page_cats = cats + [None] * max(0, doc.pages - len(cats))
            if len({c for c in cats if c}) >= 2:
                doc.mixed = True
                doc.needs_review = True
                doc.reason = ("Pages appear to belong to different document types - use 'Split pages'. "
                              + doc.reason)
        if doc.convert_flag:
            doc.needs_review = True
            doc.reason = (doc.reason + " Check the converted PDF against the original.").strip()

    # ---- document operations ----------------------------------------------------------
    def get(self, doc_id: str) -> Doc | None:
        return next((d for d in self.docs if d.id == doc_id), None)

    def remove(self, doc_id: str) -> None:
        d = self.get(doc_id)
        if d:
            self.docs.remove(d)
            self._dirty()

    def set_category(self, doc_id: str, category: str | None) -> None:
        d = self.get(doc_id)
        if not d:
            return
        if category is not None and category not in S.CATEGORIES:
            raise ValueError("Unknown category.")
        d.category = category
        d.needs_review = category is None
        d.confidence = "user" if category else "none"
        d.reason = "Category set by operator." if category else "Please choose a category."
        self._dirty()

    def confirm(self, doc_id: str) -> None:
        d = self.get(doc_id)
        if d and d.category:
            d.needs_review = False
            d.confidence = "user"
            d.reason = "Confirmed by operator."
            self._dirty()

    def rename(self, doc_id: str, name: str) -> None:
        d = self.get(doc_id)
        if d:
            d.display_name = name.strip() or Path(d.original_name).stem
            self.touch()

    def move(self, doc_id: str, delta: int) -> None:
        """Move a document up (-1) or down (+1) within its category."""
        d = self.get(doc_id)
        if not d:
            return
        peers = sorted((x for x in self.docs if x.category == d.category), key=lambda x: x.order)
        i = peers.index(d)
        j = i + delta
        if 0 <= j < len(peers):
            d.order, peers[j].order = peers[j].order, d.order
            self._dirty()

    def suggest_page_categories(self, doc_id: str) -> list[str | None]:
        """Detected page categories; pages without a signal inherit from the previous page."""
        d = self.get(doc_id)
        if not d:
            return []
        out, prev = [], d.category
        for i in range(d.pages):
            c = d.page_cats[i] if i < len(d.page_cats) else None
            prev = c or prev
            out.append(prev)
        return out

    def split(self, doc_id: str, page_cats: list[str | None]) -> list[Doc]:
        """Split a PDF so each page goes to exactly one category. No page may be left unassigned."""
        d = self.get(doc_id)
        if not d or not d.pdf_path:
            raise ValueError("Document not found.")
        if len(page_cats) != d.pages:
            raise ValueError("Every page needs a category.")
        missing = [i + 1 for i, c in enumerate(page_cats) if c not in S.CATEGORIES]
        if missing:
            raise ValueError(f"Choose a category for every page before splitting (missing: page {compact_ranges(missing)}).")
        groups: dict[str, list[int]] = {}
        for i, c in enumerate(page_cats, start=1):
            groups.setdefault(c, []).append(i)
        if len(groups) == 1:
            self.set_category(doc_id, next(iter(groups)))
            self.confirm(doc_id)
            d.mixed = False
            return [d]
        reader = PdfReader(str(d.pdf_path), strict=False)
        parts: list[Doc] = []
        for k, (cat, pages) in enumerate(groups.items()):
            w = PdfWriter()
            for p in pages:
                w.add_page(reader.pages[p - 1])
            pid = uuid.uuid4().hex[:10]
            out = self.dir / "pdf" / f"{pid}.pdf"
            with open(out, "wb") as fh:
                w.write(fh)
            nd = Doc(id=pid, original_name=d.original_name, source=f"{d.source} (split: pages {compact_ranges(pages)})",
                     ext=".pdf", orig_path=d.orig_path, sha256=f"{d.sha256}:{cat}",
                     display_name=f"{d.display_name} [{cat} p{compact_ranges(pages)}]",
                     pdf_path=out, pages=len(pages), category=cat, confidence="user",
                     needs_review=False, reason="Created by splitting a mixed PDF.",
                     order=d.order + 0.001 * (k + 1))
            parts.append(nd)
        if sum(p.pages for p in parts) != d.pages:   # belt and braces: nothing may be lost
            for p in parts:
                p.pdf_path.unlink(missing_ok=True)
            raise ValueError("Split check failed: page counts do not add up. Nothing was changed.")
        idx = self.docs.index(d)
        self.docs[idx:idx + 1] = parts
        self._dirty()
        return parts

    # ---- consolidation + generation ----------------------------------------------------
    def groups(self) -> dict[str, list[Doc]]:
        g: dict[str, list[Doc]] = {}
        for cat in S.CATEGORIES:
            docs = sorted((d for d in self.docs if d.category == cat), key=lambda x: x.order)
            if docs:
                g[cat] = docs
        return g

    def overall_status(self) -> str:
        if not self.docs:
            return "No documents yet"
        if any(d.status == "Conversion Failed" for d in self.docs):
            return "Conversion Failed - fix or remove the failed documents"
        if any(d.status == "Needs Review" for d in self.docs):
            n = sum(d.status == "Needs Review" for d in self.docs)
            return f"Needs Review ({n})"
        if self.outputs:
            return "Generated"
        return "Ready to Generate"

    def blocking_issues(self) -> list[str]:
        issues = []
        if not self.docs:
            issues.append("No documents have been uploaded.")
        for d in self.docs:
            if d.error:
                issues.append(f"'{d.original_name}' failed: {d.error} Remove it or upload a corrected file.")
            elif d.pdf_path is None:
                issues.append(f"'{d.original_name}' has not been converted to PDF yet.")
            elif d.category is None:
                issues.append(f"'{d.display_name}' has no category.")
            elif d.needs_review:
                issues.append(f"'{d.display_name}' needs review: confirm or change its category.")
        return issues

    def generate(self, hawb_raw: str, progress: Callable[[float, str], None] | None = None
                 ) -> tuple[list[Output], list[str]]:
        """Validate, group by category, merge, write <HAWB>_<SUFFIX>.pdf. Returns (outputs, errors)."""
        self.outputs, self.gen_errors, self.generated_for = [], [], ""
        hawb, errors, _ = validate_hawb(hawb_raw)
        errors = errors + self.blocking_issues()
        if errors:
            self.gen_errors = errors
            return [], errors
        out_dir = self.dir / "out"
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir()
        groups = self.groups()
        outputs: list[Output] = []
        for n, (cat, docs) in enumerate(groups.items()):
            if progress:
                progress(n / max(1, len(groups)), f"Merging {S.CATEGORIES[cat]['label']} ({len(docs)} document(s))...")
            writer, expected = PdfWriter(), 0
            try:
                for d in docs:
                    r = _open_pdf(d.pdf_path)
                    expected += len(r.pages)
                    writer.append(r)
                fname = f"{hawb}_{S.CATEGORIES[cat]['suffix']}.pdf"
                path = out_dir / fname
                with open(path, "wb") as fh:
                    writer.write(fh)
                check = len(PdfReader(str(path)).pages)
                if check != expected or check == 0:
                    raise ValueError(f"page count mismatch (expected {expected}, got {check})")
            except Exception as e:
                msg = f"Could not merge {S.CATEGORIES[cat]['label']} documents: {e}"
                errors.append(msg)
                continue
            outputs.append(Output(cat, fname, path, check, path.stat().st_size))
        if progress:
            progress(1.0, "Done")
        if errors:
            for o in outputs:
                o.path.unlink(missing_ok=True)
            self.gen_errors = errors
            return [], errors
        self.outputs, self.generated_for = outputs, hawb
        self.touch()
        return outputs, []


def zip_outputs(outputs: list[Output]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for o in outputs:
            zf.write(o.path, o.filename)
    return buf.getvalue()
