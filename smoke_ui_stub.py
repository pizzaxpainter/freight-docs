"""Smoke test of app.py against a FAKE streamlit module.

This only proves app.py's Python logic runs end-to-end (no NameErrors, correct calls into the
engine). It does NOT test real Streamlit rendering, widgets or browsers - do that on the deployed app.

Run:  python tests/smoke_ui_stub.py
"""
import io
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import invoice, make_pdf, packing  # noqa: E402


class State(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


class Fake:
    """Stands in for the `streamlit` module and for column/container objects."""

    def __init__(self, uploads, press):
        self.session_state = State()
        self.uploads, self.press = uploads, press
        self.calls = []
        self.column_config = MagicMock()
        self.sidebar = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cache_data(self, **kw):
        return lambda fn: fn

    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(n)]

    def file_uploader(self, *a, **kw):
        return self.uploads

    def text_input(self, label, **kw):
        return {"HAWB number": "sin01234567"}.get(label, kw.get("value", ""))

    def button(self, label, **kw):
        self.calls.append(("button", label))
        return label in self.press

    def download_button(self, label, **kw):
        self.calls.append(("download", label, kw.get("file_name")))
        return False

    def checkbox(self, *a, **kw):
        return kw.get("value", False)

    def selectbox(self, label, options, **kw):
        return options[kw.get("index", 0)]

    def select_slider(self, *a, **kw):
        return kw.get("value")

    def number_input(self, label, mn, mx, val, **kw):
        return val

    def progress(self, *a, **kw):
        return self

    def rerun(self):
        raise RuntimeError("rerun")

    def stop(self):
        raise SystemExit

    def data_editor(self, df, **kw):
        return df

    def __getattr__(self, name):
        def f(*a, **kw):
            self.calls.append((name, a[:1]))
            return MagicMock()
        return f


class Up:
    def __init__(self, name, data):
        self.name, self._d, self.size, self.file_id = name, data, len(data), f"id-{name}"

    def getvalue(self):
        return self._d


def zbytes(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


def run(fake):
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    mod = types.ModuleType("streamlit")
    mod.__dict__.update({k: getattr(fake, k) for k in
                         ("session_state", "columns", "file_uploader", "text_input", "button", "download_button",
                          "checkbox", "selectbox", "select_slider", "number_input", "progress", "rerun", "stop",
                          "data_editor", "cache_data", "column_config", "sidebar")})
    mod.__getattr__ = lambda name: getattr(fake, name)
    sys.modules["streamlit"] = mod
    code = compile(src, "app.py", "exec")
    exec(code, {"__name__": "__main__"})


if __name__ == "__main__":
    ups = [Up("cust.zip", zbytes({"f/Invoice A.pdf": invoice("A"), "f/Packing List A.pdf": packing("A")})),
           Up("Invoice B.pdf", invoice("B")),
           Up("scan.pdf", make_pdf([["no keywords"]]))]
    fake = Fake(ups, press={"Generate Final PDFs"})
    run(fake)                                  # first pass: upload + convert, generate blocked by review item
    ws = fake.session_state["ws"]
    print("docs:", [(d.original_name, d.status) for d in ws.docs])
    assert len(ws.docs) == 4, ws.docs
    assert ws.gen_errors and any("scan" in e for e in ws.gen_errors), ws.gen_errors
    ws.remove([d for d in ws.docs if d.original_name == "scan.pdf"][0].id)
    fake.uploads = ups[:2]
    run(fake)                                  # second pass: generate succeeds, download buttons rendered
    print("outputs:", [o.filename for o in ws.outputs])
    assert [o.filename for o in ws.outputs] == ["SIN01234567_CI.pdf", "SIN01234567_PL.pdf"]
    assert any(c[0] == "download" and c[2] == "SIN01234567_CI.pdf" for c in fake.calls)
    ws.cleanup()
    print("UI stub smoke test passed (logic only; not a real Streamlit run)")
