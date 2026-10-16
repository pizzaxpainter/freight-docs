"""Freight Document Processor - Streamlit UI.

Flow: Enter HAWB -> Upload ZIPs/files -> automatic conversion + classification
      -> review exceptions -> Generate PDFs -> download.
No database: everything lives in a per-session temporary folder that is deleted automatically.
"""
import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import settings as S
from core import Workspace, human_size, render_pages, validate_hawb, zip_outputs

st.set_page_config(page_title=S.APP_TITLE, page_icon="📦", layout="wide")

CAT_CODES = list(S.CATEGORIES)
STATUS_COLOUR = {"PDF Ready": "green", "Needs Review": "orange", "Conversion Failed": "red", "Uploaded": "gray"}


def cat_label(code):
    if code is None:
        return "Select category..."
    c = S.CATEGORIES[code]
    return f"{code} - {c['label']}"


# --------------------------------------------------------------------------------------
# Optional shared-password gate (use company SSO in front of the app where possible)
# --------------------------------------------------------------------------------------
def password_gate():
    pw = os.environ.get("APP_PASSWORD")
    if not pw or st.session_state.get("authed"):
        return
    st.title(S.APP_TITLE)
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), pw.encode()):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def get_ws() -> Workspace:
    if "ws" not in st.session_state:
        Workspace.purge_old()
        st.session_state["ws"] = Workspace()
        st.session_state.setdefault("uploader_key", 0)
    ws = st.session_state["ws"]
    if not ws.dir.exists():     # folder was purged after a long idle period
        st.session_state["ws"] = ws = Workspace()
        st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
    ws.touch()
    return ws


# --------------------------------------------------------------------------------------
# Callbacks
# --------------------------------------------------------------------------------------
def on_category(doc_id, key):
    st.session_state["ws"].set_category(doc_id, st.session_state.get(key))


def on_name(doc_id):
    st.session_state["ws"].rename(doc_id, st.session_state.get(f"name_{doc_id}", ""))


def on_confirm(doc_id):
    st.session_state["ws"].confirm(doc_id)


def on_remove(doc_id):
    st.session_state["ws"].remove(doc_id)


def on_move(doc_id, delta):
    st.session_state["ws"].move(doc_id, delta)


def on_new_shipment():
    st.session_state["ws"].cleanup()
    next_key = st.session_state.get("uploader_key", 0) + 1     # fresh, empty file uploader
    for k in list(st.session_state.keys()):
        if k != "authed":
            del st.session_state[k]
    st.session_state["uploader_key"] = next_key


@st.cache_data(max_entries=64, ttl=900, show_spinner=False)
def cached_render(path: str, mtime: float, dpi: int, first: int, last: int):
    return render_pages(Path(path), dpi, first, last)


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------
def section_shipment(ws: Workspace):
    st.subheader("1. Shipment information")
    c1, c2 = st.columns([2, 3])
    raw = c1.text_input("HAWB number", key="hawb", placeholder="e.g. SIN01234567",
                        help="Used for the final file names. You can upload documents first and enter it later.")
    hawb, errs, warns = validate_hawb(raw)
    if raw and errs:
        c1.error(errs[0])
    for w in warns:
        c1.warning(w)
    c2.metric("Status", ws.overall_status())
    return raw


def section_upload(ws: Workspace):
    st.subheader("2. Upload documents")
    files = st.file_uploader(
        "Drag and drop ZIP, PDF, Word or Excel files here, or click Browse files",
        type=["zip", "pdf", "doc", "docx", "xls", "xlsx"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.get('uploader_key', 0)}",
        help="You can add more files at any time; everything is combined into this one shipment.",
    )
    new = [f for f in files if (getattr(f, "file_id", None) or (f.name, f.size)) not in ws.seen_upload_ids]
    if new:
        bar = st.progress(0.0, text="Reading uploaded files...")
        added = []
        for f in new:
            docs, _ = ws.ingest(f.name, f.getvalue())
            ws.seen_upload_ids.add(getattr(f, "file_id", None) or (f.name, f.size))
            added += docs
        for i, d in enumerate(added):
            bar.progress(i / max(1, len(added)), text=f"Converting and classifying {i + 1}/{len(added)}: {d.original_name}")
            ws.process(d)
        bar.empty()
    n_err = sum(1 for lvl, _ in ws.log if lvl == "error")
    c1, c2, c3 = st.columns(3)
    c1.metric("Files uploaded", len(ws.seen_upload_ids))
    c2.metric("Documents found", len(ws.docs))
    c3.metric("Problems", n_err + sum(1 for d in ws.docs if d.status == "Conversion Failed"))
    if ws.log:
        with st.expander(f"Upload messages ({len(ws.log)})", expanded=bool(n_err)):
            for lvl, text in ws.log:
                {"error": st.error, "warning": st.warning}.get(lvl, st.info)(text)
            st.button("Clear messages", on_click=lambda: ws.log.clear())


def show_preview(doc):
    if doc.pdf_path is None:
        st.error(doc.error or "No PDF is available for this document.")
        return
    c1, c2, c3 = st.columns([1, 1, 2])
    dpi = c1.select_slider("Zoom", options=[60, 90, 130, 180], value=90, key=f"zoom_{doc.id}")
    first = c2.number_input("Start at page", 1, max(1, doc.pages), 1, key=f"first_{doc.id}")
    last = min(doc.pages, int(first) + 4)
    c3.caption(f"Showing pages {int(first)}-{last} of {doc.pages} (5 pages at a time)")
    try:
        imgs = cached_render(str(doc.pdf_path), doc.pdf_path.stat().st_mtime, dpi, int(first), last)
        for i, png in enumerate(imgs):
            st.image(png, caption=f"Page {int(first) + i}")
    except Exception:
        st.warning("The page preview could not be shown. Use the button below to open the PDF in a new window.")
    st.download_button("Open / download this PDF", data=doc.pdf_path.read_bytes(),
                       file_name=f"{doc.display_name or 'document'}.pdf", mime="application/pdf",
                       key=f"dlprev_{doc.id}", on_click="ignore")


def show_split(ws: Workspace, doc):
    st.markdown("**Assign every page to a category.** Pre-filled from the page contents - please check it.")
    df = pd.DataFrame({"Page": range(1, doc.pages + 1), "Category": ws.suggest_page_categories(doc.id)})
    edited = st.data_editor(
        df, hide_index=True, disabled=["Page"], key=f"splitedit_{doc.id}",
        column_config={"Category": st.column_config.SelectboxColumn("Category", options=CAT_CODES, required=False)},
    )
    if st.button("Apply split", key=f"applysplit_{doc.id}", type="primary"):
        cats = [None if pd.isna(c) else c for c in edited["Category"]]
        try:
            ws.split(doc.id, cats)
            st.rerun()
        except ValueError as e:
            st.error(str(e))


def section_review(ws: Workspace):
    st.subheader("3. Review and categorise")
    if not ws.docs:
        st.info("Documents appear here after you upload them.")
        return
    for doc in sorted(ws.docs, key=lambda d: d.order):
        status = doc.status
        with st.container(border=True):
            top = st.columns([4, 3, 1.4, 1.4])
            top[0].markdown(f"**{doc.original_name}**  \n:{STATUS_COLOUR.get(status, 'gray')}[{status}] · "
                            f"{doc.format} · {doc.pages or '-'} page(s)")
            top[0].caption(f"Source: {doc.source}")
            cat_key = f"cat_{doc.id}_{doc.category or 'none'}"   # key follows the value so it never goes stale
            top[1].selectbox("Category", [None] + CAT_CODES, index=([None] + CAT_CODES).index(doc.category),
                             format_func=cat_label, key=cat_key, on_change=on_category, args=(doc.id, cat_key))
            if status == "Needs Review" and doc.category:
                top[2].button("✓ Confirm", key=f"conf_{doc.id}", on_click=on_confirm, args=(doc.id,), type="primary")
            top[3].button("Remove", key=f"rm_{doc.id}", on_click=on_remove, args=(doc.id,))

            if doc.error:
                st.error(doc.error)
            elif doc.needs_review:
                st.warning(doc.reason or "Please confirm the category.")
            for w in doc.warnings:
                st.caption(f"⚠ {w}")

            if not doc.error:
                r = st.columns([3, 1, 1, 3])
                r[0].text_input("Display name", value=doc.display_name, key=f"name_{doc.id}",
                                on_change=on_name, args=(doc.id,))
                show = r[1].checkbox("Preview", key=f"prev_{doc.id}")
                split = r[2].checkbox("Split pages", key=f"split_{doc.id}", disabled=doc.pages < 2,
                                      value=doc.mixed and doc.pages > 1)
                if show:
                    show_preview(doc)
                if split and doc.pages > 1:
                    show_split(ws, doc)


def section_summary(ws: Workspace):
    st.subheader("4. Consolidation summary")
    groups = ws.groups()
    unassigned = [d for d in ws.docs if d.category is None and not d.error]
    cols = st.columns(len(S.CATEGORIES))
    for col, (code, meta) in zip(cols, S.CATEGORIES.items()):
        docs = groups.get(code, [])
        col.metric(f"{meta['label']} ({code})", f"{len(docs)} doc(s)", f"{sum(d.pages for d in docs)} pages", delta_color="off")
        col.caption(f"E-SI code: {meta['esi_code']}" + ("" if meta["verified"] else " (to be verified)"))
    if unassigned:
        st.warning(f"{len(unassigned)} document(s) have no category yet and will block generation.")
    for code, docs in groups.items():
        if len(docs) > 1:
            with st.expander(f"Merge order - {S.CATEGORIES[code]['label']} ({len(docs)} documents)"):
                for i, d in enumerate(docs):
                    a, b, c = st.columns([8, 1, 1])
                    a.write(f"{i + 1}. {d.display_name}  ·  {d.pages} p")
                    b.button("▲", key=f"up_{d.id}", on_click=on_move, args=(d.id, -1), disabled=i == 0)
                    c.button("▼", key=f"dn_{d.id}", on_click=on_move, args=(d.id, 1), disabled=i == len(docs) - 1)


def section_generate(ws: Workspace, raw_hawb: str):
    st.subheader("5. Generate final PDFs")
    if st.button("Generate Final PDFs", type="primary"):
        bar = st.progress(0.0, text="Starting...")
        ws.generate(raw_hawb, progress=lambda f, t: bar.progress(min(1.0, f), text=t))
        bar.empty()
    for e in ws.gen_errors:
        st.error(e)
    hawb, _, _ = validate_hawb(raw_hawb)
    if ws.outputs and ws.generated_for != hawb:
        st.warning("The HAWB was changed after the PDFs were generated. Click Generate Final PDFs again.")
        return
    if ws.outputs:
        st.success(f"Generated {len(ws.outputs)} file(s) for {ws.generated_for}. Download them now - they are deleted automatically "
                   f"after {S.RETENTION_MINUTES} minutes of inactivity and are not stored anywhere else.")
        for o in ws.outputs:
            a, b, c = st.columns([4, 2, 2])
            a.markdown(f"**{o.filename}**  \n{S.CATEGORIES[o.category]['label']}")
            b.write(f"{o.pages} pages · {human_size(o.size)}")
            c.download_button(f"Download {S.CATEGORIES[o.category]['suffix']}", data=o.path.read_bytes(),
                              file_name=o.filename, mime="application/pdf", key=f"dl_{o.category}",
                              on_click="ignore", type="primary")
        if len(ws.outputs) > 1:
            st.download_button("Download all (ZIP)", data=zip_outputs(ws.outputs),
                               file_name=f"{ws.generated_for}_documents.zip", mime="application/zip",
                               key="dl_all", on_click="ignore")


def main():
    password_gate()
    ws = get_ws()
    st.title(f"📦 {S.APP_TITLE}")
    st.caption("Upload from your computer, review, download the finished PDFs to your computer. "
               "Files are processed in temporary storage only - no database, nothing kept.")
    with st.sidebar:
        st.header("Session")
        st.write("Files are deleted automatically after "
                 f"{S.RETENTION_MINUTES} min of inactivity, or immediately when you start a new shipment.")
        st.button("Start new shipment (delete all files)", on_click=on_new_shipment)
    raw = section_shipment(ws)
    st.divider()
    section_upload(ws)
    st.divider()
    section_review(ws)
    st.divider()
    section_summary(ws)
    st.divider()
    section_generate(ws, raw)


main()
