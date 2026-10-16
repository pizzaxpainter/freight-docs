# Freight Document Processor

Enter HAWB -> upload ZIPs / files from your computer -> automatic extraction, PDF conversion and
classification -> review exceptions -> **Generate Final PDFs** -> download `<HAWB>_CI.pdf`, `_PL.pdf`, `_ID.pdf` ... to your computer.

**No database.** Uploaded files sit in a private temporary folder for your session only. They are deleted
when you click *Start new shipment* or after `FDP_RETENTION_MINUTES` (default 120) of inactivity.
Nothing is sent to third-party services; conversion runs on the server with LibreOffice.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit web UI |
| `core.py` | Engine: safe ZIP extraction, Office->PDF, text extraction, split, merge, naming |
| `classifier.py` | Rules-based classifier (content first, filename second, unsure = Needs Review) |
| `settings.py` | **All configuration**: categories / E-SI codes, keyword rules, limits, retention |
| `Dockerfile`, `requirements.txt`, `.streamlit/config.toml` | Deployment |
| `tests/` | Engine tests (`test_core.py`) and a logic-only UI smoke test (`smoke_ui_stub.py`) |

## Run it

**Docker (what employees will use, hosted centrally):**
```
docker build -t freight-docs .
docker run -p 8501:8501 freight-docs
```
Open http://localhost:8501.

**Without Docker (developer machine, Linux/macOS):** install LibreOffice, poppler-utils and qpdf, then
`pip install -r requirements.txt && streamlit run app.py`.

## Hosting at one permanent URL (needs IT approval)

1. Push the image to the company container registry.
2. Run it on an approved platform, e.g. **Azure Container Apps** or **Azure App Service (Linux container)**, port 8501.
3. **WebSockets must be enabled** (Streamlit needs them). Run **one instance**, or enable session affinity,
   because each session's files live on that instance's disk.
4. **HTTPS + login:** enable Microsoft Entra ID sign-in on the platform (App Service "Authentication" / Container Apps
   built-in auth) so only company staff reach the URL. As a stopgap you can set `APP_PASSWORD` for a shared password
   (this is *not* a substitute for company SSO).
5. Give the app 2 vCPU / 4 GB RAM or more; LibreOffice is the heaviest part.
6. Updates: rebuild and redeploy the image; everyone gets the new version at the same URL.

Environment variables: `FDP_WORK_DIR`, `FDP_RETENTION_MINUTES`, `FDP_MAX_FILE_MB`, `FDP_MAX_EXTRACTED_MB`, `APP_PASSWORD`.

## What the app does

* **Upload:** several ZIPs and loose files together, and more at any time. ZIP handling: nested folders and nested ZIPs,
  OS junk ignored, path-traversal blocked, size/entry/compression-ratio limits, password-protected or corrupt ZIPs reported,
  identical files skipped (reported), same filename with different content kept separately.
* **Convert:** `.doc .docx .xls .xlsx` -> PDF via LibreOffice (layout kept; every worksheet printed). Conversions with no text
  or unusually many pages are flagged for review; multi-sheet / hidden-sheet workbooks get a warning.
* **Classify:** CI / PL / ID / DG / CITES from page text (headings weigh most), filename as a weaker hint. Weak or conflicting
  matches, scanned PDFs and billing reports are **Needs Review**; generation is blocked until you confirm or change them.
* **Review:** per document - preview (rendered page images, zoom, 5 pages at a time, plus open/download fallback), category,
  display name, remove, confirm.
* **Split mixed PDFs:** page table pre-filled from page contents; every page must be assigned; the page total is re-checked.
* **Generate:** validates HAWB and all documents, merges per category in the order shown (reorderable), re-opens every output
  to verify the page count, names it `<HAWB>_<CI|PL|ID|DG|CITES>.pdf`. No file is created for an empty category.
  Individual download buttons for each file, plus an optional "Download all (ZIP)".

## Needs your confirmation (settings.py)

* The E-SI codes for **ID, DG and CITES** are placeholders (`INT`, `DGCL`, `CITES`).
* Whether **Billing Reports are Internal Documents** (currently ID, always flagged for confirmation).
* The **HAWB format** (`3 letters + 8 digits` gives a warning, not a block, if it does not match).
* Keyword rules - tune them using real customer documents.

## Tests

```
python -m unittest discover -s tests -v     # needs soffice, pdftotext, qpdf, python-docx, reportlab
python tests/smoke_ui_stub.py
```
See "Test status" below for exactly what has and has not been verified.

## Test status

Verified (23 automated tests on **synthetic** files, run on Linux with LibreOffice 24.2): nested ZIP extraction, junk / unsafe
paths / unsupported types / corrupt / password-flagged ZIPs, size limit, duplicates, `.docx .doc .xlsx .xls` conversion with
content preserved (including a second worksheet), classification rules, mixed-PDF detection and splitting (no pages lost),
multi-ZIP + loose-file consolidation into one PDF per category with correct names / order / page counts, corrupted-PDF blocking,
HAWB validation, 60-document batch, session isolation and retention purge.

**Not verified:**
* **The Streamlit UI has not been run in a real browser or real Streamlit.** The build environment could not install
  Streamlit (package index blocked). `app.py` has only been syntax-checked and exercised against a fake `streamlit` module.
  Expect to fix small UI issues on first real run.
* **The Docker image has not been built.**
* **The real `PDFHandler.zip` sample was not available**, so nothing has been tested on real customer documents.
* Real password-*encrypted* ZIPs (only the encryption flag is tested), scanned PDFs / OCR, Edge-specific behaviour, and
  concurrent multi-user load.
* The 50% time-saving target has not been measured.

## Known limitations

* No OCR: scanned PDFs always need a manual category.
* Excel sheets wider than one page follow the workbook's own print setup; very wide sheets may split across pages (flagged when long).
* Page-level split detection is keyword-based; operators must check the pre-filled table.
* Linux/Docker only (uses POSIX process control).
* Sessions live on one server instance; restart or redeploy loses in-progress work (by design, nothing is persisted).
