import os
import re
import io
import sys
import subprocess
from pathlib import Path

import streamlit as st

# ---------- UI ----------
st.set_page_config(page_title="Auto Doc Gen — Streamlit", layout="wide")
st.title("🧠 Auto Doc Gen — Streamlit")

with st.sidebar:
    st.header("Config")
    repo_url = st.text_input(
        "GitHub repository URL",
        placeholder="https://github.com/org/repo"
    )
    run_btn = st.button("Generate Documentation", type="primary")

log_area = st.empty()
preview = st.container()
download = st.container()

# ---------- Markdown cleaning helpers (UI-only; generator stays untouched) ----------

HEADING_RE = re.compile(r'^\s*(#{1,6})\s+(.*\S)\s*$', re.M)
LEADING_H_RE = re.compile(r'^\s*(#{1,6})\s+(.*\S)\s*$')

def _collapse_leading_duplicate_headings(md: str) -> str:
    """
    If the file starts with the same heading repeated (even with blank lines),
    keep the first one and drop the rest until real content appears.
    """
    lines = md.splitlines()
    # find first non-empty line
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i >= len(lines):
        return md  # all blank

    m = LEADING_H_RE.match(lines[i])
    if not m:
        return md  # doesn't start with a heading

    first_h_text = m.group(2).strip().lower()

    # mark subsequent blank lines and identical headings to drop
    j = i + 1
    to_drop = []
    while j < len(lines):
        if lines[j].strip() == "":
            to_drop.append(j)
            j += 1
            continue
        mj = LEADING_H_RE.match(lines[j])
        if mj and mj.group(2).strip().lower() == first_h_text:
            to_drop.append(j)
            j += 1
            continue
        break

    if not to_drop:
        return md

    kept = []
    drop_set = set(to_drop)
    for idx, line in enumerate(lines):
        if idx in drop_set:
            continue
        kept.append(line)
    return "\n".join(kept)

def _dedupe_consecutive_headings(md: str) -> str:
    """Remove consecutive duplicate markdown headings (exact text match)."""
    lines = md.splitlines()
    out = []
    last_heading_text = None
    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            text = m.group(2).strip()
            if last_heading_text is not None and text.lower() == last_heading_text.lower():
                # skip duplicate consecutive heading
                continue
            last_heading_text = text
        else:
            last_heading_text = None
        out.append(line)
    return "\n".join(out)

def _strip_slug_prefix(md: str, filename_stem: str) -> str:
    """
    If the first non-empty line is a slug (e.g., 'objective_and_scope')
    or exactly the filename stem (normalized), drop it.
    """
    lines = md.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines):
        first = lines[i].strip()
        if re.fullmatch(r'[a-z0-9_]+', first) or first.replace(" ", "_").lower() == filename_stem.lower():
            del lines[i]
    return "\n".join(lines)

def _squeeze_blank_lines(md: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', md).strip()

def clean_section_md(md: str, filename_stem: str) -> str:
    """Apply all cleanups: collapse leading dup headings, drop slug, dedupe, squeeze."""
    md = _collapse_leading_duplicate_headings(md)
    md = _strip_slug_prefix(md, filename_stem)
    md = _dedupe_consecutive_headings(md)
    md = _squeeze_blank_lines(md)
    return md

def starts_with_heading(md: str) -> bool:
    """True if the first non-empty line is a markdown heading."""
    for line in md.splitlines():
        if line.strip() == "":
            continue
        return HEADING_RE.match(line) is not None
    return False

def humanize_stem(stem: str) -> str:
    """objective_and_scope -> Objective And Scope (best-effort)."""
    words = re.split(r'[_\-]+', stem)
    return " ".join(w.capitalize() for w in words if w)

def combine_markdown(files):
    """Combine cleaned sections; only add our own H1 if section doesn't start with one."""
    parts = []
    for p in files:
        p = Path(p)
        raw = p.read_text(encoding="utf-8", errors="ignore")
        cleaned = clean_section_md(raw, p.stem)
        if starts_with_heading(cleaned):
            parts.append(cleaned)
        else:
            parts.append(f"# {humanize_stem(p.stem)}\n\n{cleaned}")
    return "\n\n---\n\n".join(parts)

def naive_markdown_to_docx(md_text: str) -> bytes:
    """
    Very simple Markdown→DOCX using python-docx.
    For production-grade output, consider Pandoc; this keeps it dependency-light.
    """
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    for line in md_text.splitlines():
        if line.startswith("# "):
            r = doc.add_paragraph().add_run(line[2:].strip()); r.bold = True; r.font.size = Pt(16)
        elif line.startswith("## "):
            r = doc.add_paragraph().add_run(line[3:].strip()); r.bold = True; r.font.size = Pt(14)
        elif line.startswith("### "):
            r = doc.add_paragraph().add_run(line[4:].strip()); r.bold = True; r.font.size = Pt(12)
        elif line.strip().startswith(("-", "*")):
            doc.add_paragraph(line.lstrip("-* ").strip(), style="List Bullet")
        elif line.strip().startswith("```"):
            doc.add_paragraph("")  # ignore fence markers
        else:
            doc.add_paragraph(line)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ---------- Subprocess launcher for app/main.py ----------

def run_main_and_collect(repo_url: str):
    """
    Calls app/main.py with --repo <url>, streams logs, and collects file paths
    printed as 'Wrote: <path>'. Windows-safe; prefers venv Python.
    """
    base_dir = Path(__file__).parent.resolve()
    app_dir = (base_dir / "app").resolve()
    main_py = (app_dir / "main.py").resolve()

    if not main_py.exists():
        raise FileNotFoundError(f"Cannot find main.py at: {main_py}")

    # Prefer project venv Python if available, else fall back to current interpreter
    venv_py = base_dir / ".venv" / "Scripts" / "python.exe"  # Windows
    py_exec = str(venv_py) if venv_py.exists() else sys.executable

    cmd = [py_exec, "-u", str(main_py), "--repo", repo_url]

    # Force UTF-8 to avoid UnicodeEncodeError with emojis on Windows
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    proc = subprocess.Popen(
        cmd,
        cwd=str(base_dir),                      # repo root as working dir
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    wrote_paths = []
    log_lines = []
    wrote_re = re.compile(r"^Wrote:\s*(.+)$")

    for line in proc.stdout:
        log_lines.append(line.rstrip("\n"))
        # stream last N lines to UI
        log_area.code("\n".join(log_lines[-400:]))

        m = wrote_re.match(line.strip())
        if m:
            p = Path(m.group(1)).expanduser()
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            wrote_paths.append(p)

    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"main.py exited with code {ret}")

    return wrote_paths, "\n".join(log_lines)

# ---------- Main button action ----------

if run_btn:
    if not repo_url:
        st.error("Please enter a GitHub repository URL.")
        st.stop()

    try:
        with st.spinner("Running main.py…"):
            wrote_files, logs = run_main_and_collect(repo_url)

        if not wrote_files:
            st.warning("No 'Wrote:' file paths were captured from main.py output.")
        else:
            st.success("Generation completed.")

            # ---- Preview with heading de-dup ----
            with preview:
                st.subheader("Preview")
                for p in wrote_files:
                    raw = Path(p).read_text(encoding="utf-8", errors="ignore")
                    cleaned = clean_section_md(raw, Path(p).stem)
                    if not starts_with_heading(cleaned):
                        st.markdown(f"# {humanize_stem(Path(p).stem)}")
                    st.markdown(cleaned)

            # ---- Downloads (cleaned & combined) ----
            combined_md = combine_markdown(wrote_files)
            with download:
                st.subheader("Download")
                st.download_button(
                    "⬇️ Markdown",
                    data=combined_md.encode("utf-8"),
                    file_name="Technical_Documentation.md",
                    mime="text/markdown",
                )
                try:
                    docx_bytes = naive_markdown_to_docx(combined_md)
                    st.download_button(
                        "⬇️ DOCX (basic)",
                        data=docx_bytes,
                        file_name="Technical_Documentation.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                except Exception as e:
                    st.info(f"DOCX export skipped: {e}")

    except Exception as e:
        import traceback
        st.error("An error occurred during generation.")
        st.exception(e)
        st.code(traceback.format_exc())
else:
    st.info("Enter a repo URL and click **Generate Documentation**.")
