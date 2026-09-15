#!/usr/bin/env python3
"""
Compile a LaTeX project locally with latexmk (no network access).
Usage: python compile.py <work_dir> <main_tex> <output_pdf_path>

main_tex: path relative to work_dir (e.g. ms.tex), or absolute path to the main file.
output_pdf_path: full path for the output PDF; if an existing directory is passed, write <main_basename>.pdf there.
"""
import os
import re
import sys
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path


_BIBTEX_CMD_RE = re.compile(r"(?P<indent>^[ \t]*)\\bibliography\s*\{(?P<names>[^}]+)\}", re.MULTILINE)
_THEBIB_RE = re.compile(r"\\begin\{thebibliography\}")
_BBL_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{[^}]+\.bbl\}")
_CMD_ALREADY_DEFINED_RE = re.compile(r"LaTeX Error: Command \\([A-Za-z@]+) already defined")
_CMD_ALREADY_DEFINED_WITH_PATH_RE = re.compile(
    r"^\./(?P<path>[^:\n]+):(?P<lineno>\d+):\s+LaTeX Error: Command \\(?P<cmd>[A-Za-z@]+) already defined",
    re.MULTILINE,
)
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\{document\}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SOURCE_TEXT_EXTS = {
    ".tex",
    ".sty",
    ".cls",
    ".bst",
    ".bib",
    ".bbx",
    ".cbx",
    ".cfg",
}
_BUILD_ARTIFACT_EXTS = (
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".lof",
    ".lot",
    ".nav",
    ".snm",
    ".vrb",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".run.xml",
    ".bcf",
    ".blg",
    ".idx",
    ".ilg",
    ".ind",
    ".xdv",
    ".dvi",
)
_SKIP_FILENAMES = {"download.env"}
_INLINED_BBL_MARKER = "% arxiv-translator: inlined prebuilt .bbl"
_AUTO_CJK_PREAMBLE = r"""
% arxiv-translator: local CJK support (TeX Live Fandol fonts)
\usepackage{fontspec}
\usepackage{luatexja}
\usepackage{luatexja-fontspec}
\setmainjfont{FandolSong-Regular.otf}[
  BoldFont=FandolSong-Bold.otf,ItalicFont=FandolKai-Regular.otf,
  BoldItalicFont=FandolKai-Regular.otf,BoldItalicFeatures={FakeBold=2}]
\setsansjfont{FandolHei-Regular.otf}[BoldFont=FandolHei-Bold.otf]
\setmonojfont{FandolFang-Regular.otf}
"""
_COMPILE_TIMEOUT = 300
_BUILD_DIR = ".arxiv-build"
_PACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_UNRESOLVED_LOG_RE = re.compile(
    r"(?:LaTeX|Package \S+) Warning: (?:There were undefined (?:references|citations)|"
    r"(?:Citation|Reference) [^\n]+ undefined|Label\(s\) may have changed|Please \(re\)run Biber)",
)


def _uncomment(text):
    # Preserve offsets so matches in active text can be applied to the original.
    return re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group()), text)


def _packages(text):
    return {name.strip() for match in _PACKAGE_RE.finditer(_uncomment(text))
            for name in match.group(1).split(",")}


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _norm_relpath(path, root):
    rel = os.path.relpath(path, root)
    rel = os.path.normpath(rel)
    if os.name == "nt":
        rel = rel.replace("\\", "/")
    return rel


def _iter_project_files(work_dir):
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in {_BUILD_DIR, ".git", "__pycache__"}]
        for fname in files:
            abs_path = os.path.join(root, fname)
            yield abs_path, _norm_relpath(abs_path, work_dir)


def _collect_source_texts(work_dir):
    texts = {}
    for abs_path, rel in _iter_project_files(work_dir):
        if os.path.splitext(rel)[1].lower() not in _SOURCE_TEXT_EXTS:
            continue
        try:
            texts[rel] = _read_text(abs_path)
        except OSError:
            continue
    return texts


def _main_tex_relative(work_dir, main_tex):
    root = Path(work_dir).expanduser().resolve()
    main = Path(main_tex).expanduser()
    main = (main if main.is_absolute() else root / main).resolve()
    if not root.is_dir():
        raise ValueError(f"work_dir is not a directory: {root}")
    try:
        rel = main.relative_to(root)
    except ValueError:
        raise ValueError(f"main file must be inside work_dir: {main}") from None
    if not main.is_file() or main.suffix.lower() != ".tex":
        raise ValueError(f"main file is not an existing .tex file: {main}")
    return str(root), rel.as_posix()


def _resolve_output_pdf(output_path, main_tex_rel):
    output = Path(output_path).expanduser()
    if output_path.endswith(os.sep) or output.is_dir():
        output = output / (Path(main_tex_rel).stem + ".pdf")
    return str(output.absolute())


def _find_prebuilt_bbl(work_dir, main_rel, bibliography_names=None):
    bibliography_names = bibliography_names or []
    wanted_stems = []
    for raw in bibliography_names:
        stem = os.path.splitext(os.path.basename(raw.strip()))[0].lower()
        if stem:
            wanted_stems.append(stem)
    main_stem = os.path.splitext(os.path.basename(main_rel))[0].lower()
    bbl_files = []
    for _, rel in _iter_project_files(work_dir):
        if rel.lower().endswith(".bbl"):
            bbl_files.append(rel)
    if not bbl_files:
        return None
    for wanted in wanted_stems:
        for rel in bbl_files:
            if os.path.splitext(os.path.basename(rel))[0].lower() == wanted:
                return rel
    for rel in bbl_files:
        if os.path.splitext(os.path.basename(rel))[0].lower() == main_stem:
            return rel
    if len(bbl_files) == 1:
        return bbl_files[0]
    return None


def _split_bibliography_names(raw):
    return [name.strip() for name in raw.split(",") if name.strip()]


def _inline_prebuilt_bbl(work_dir, main_rel):
    """Reuse a shipped classic BibTeX bibliography without regenerating it."""
    source_texts = _collect_source_texts(work_dir)
    tex_blob = "\n".join(text for rel, text in source_texts.items() if rel.lower().endswith(".tex"))

    if "biblatex" in _packages(tex_blob) or "\\addbibresource" in _uncomment(tex_blob):
        return False
    if _THEBIB_RE.search(tex_blob) or _BBL_INPUT_RE.search(tex_blob) or _INLINED_BBL_MARKER in tex_blob:
        return False

    changed = False
    for rel, text in source_texts.items():
        if not rel.lower().endswith(".tex"):
            continue

        def _replace(match):
            nonlocal changed
            names = _split_bibliography_names(match.group("names"))
            bbl_rel = _find_prebuilt_bbl(work_dir, main_rel, names)
            if not bbl_rel:
                return match.group(0)
            try:
                bbl_text = _read_text(os.path.join(work_dir, bbl_rel)).strip()
            except OSError:
                return match.group(0)
            changed = True
            indent = match.group("indent") or ""
            return (
                f"{indent}{_INLINED_BBL_MARKER}: {bbl_rel}\n"
                f"{indent}% {match.group(0).lstrip()}\n"
                f"{bbl_text}\n"
                f"{indent}% arxiv-translator: end inlined prebuilt .bbl"
            )

        new_text, n = _BIBTEX_CMD_RE.subn(_replace, text, count=1)
        if n and new_text != text:
            with open(os.path.join(work_dir, rel), "w", encoding="utf-8") as f:
                f.write(new_text)
            return True

    return changed


def _detect_compiler(work_dir, main_rel):
    text = _uncomment(_read_text(os.path.join(work_dir, main_rel)))
    if "xeCJK" in _packages(text) or "\\setCJKmainfont" in text:
        return "xelatex"
    return "lualatex"


def _project_contains_cjk(work_dir):
    for rel, text in _collect_source_texts(work_dir).items():
        if rel.lower().endswith(".tex") and _CJK_RE.search(text):
            return True
    return False


def _ensure_cjk_support(work_dir, main_rel):
    path = os.path.join(work_dir, main_rel)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False

    if not _project_contains_cjk(work_dir):
        return False

    # Respect existing CJK support, including package options and ctex classes.
    active_text = _uncomment(text)
    if (_packages(text) & {"luatexja", "luatexja-fontspec", "xeCJK", "ctex", "CJK", "CJKutf8"}
            or re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex[^}]*\}", active_text)
            or any(tok in active_text for tok in ("\\setmainjfont{", "\\setCJKmainfont{"))):
        return False

    begin = _BEGIN_DOCUMENT_RE.search(active_text)
    if not begin:
        return False

    preamble = _AUTO_CJK_PREAMBLE
    if "fontspec" in _packages(text):
        preamble = preamble.replace("\\usepackage{fontspec}\n", "", 1)

    new_text = text[:begin.start()] + preamble + text[begin.start():]

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError:
        return False

    return True


def _preflight_comment_inputenc_fontenc(work_dir, main_rel):
    # When using XeLaTeX/LuaLaTeX stacks (fontspec / xeCJK / luatexja),
    # inputenc/fontenc frequently cause compilation issues.
    path = os.path.join(work_dir, main_rel)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False

    uses_unicode_stack = bool(_packages(text) & {"fontspec", "xeCJK", "luatexja", "ctex"})
    if not uses_unicode_stack:
        return False

    changed = False
    # Comment only if the line is not already commented out.
    def _comment_line(m):
        indent = m.group("indent") or ""
        line = m.group(0)
        # Keep indentation, comment the rest of the line.
        return indent + "% " + line[len(indent) :]

    for pat in (
        r"^(?P<indent>\s*)\\usepackage(?:\[[^\]]*\])?\{inputenc\}.*$",
        r"^(?P<indent>\s*)\\usepackage(?:\[[^\]]*\])?\{fontenc\}.*$",
    ):
        if re.search(pat, text, flags=re.MULTILINE):
            text = re.sub(pat, _comment_line, text, count=1, flags=re.MULTILINE)
            changed = True

    if changed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            return False
    return changed


def _fix_command_already_defined(work_dir, rel_path, cmd):
    # Prefer \renewcommand so the paper's intended macro definition wins.
    # This is safer than \providecommand for math macros commonly redefined in arXiv sources.
    abs_path = os.path.realpath(os.path.join(work_dir, rel_path))
    if os.path.commonpath([os.path.realpath(work_dir), abs_path]) != os.path.realpath(work_dir):
        return False
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return False

    cmd_esc = re.escape(cmd)
    pat1 = re.compile(rf"^\s*\\newcommand\*?\s*\\{cmd_esc}\b")
    pat2 = re.compile(rf"^\s*\\newcommand\*?\s*\{{\\{cmd_esc}\}}")

    changed = False
    for i, line in enumerate(lines):
        if pat1.search(line) or pat2.search(line):
            # Replace only the defining primitive; keep the rest (args/body) intact.
            lines[i] = re.sub(r"\\newcommand\*?", r"\\renewcommand", line, count=1)
            changed = True
            break

    if not changed:
        return False

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        return False

    return True


def _try_fix_from_logs(work_dir, main_rel, logs_text):
    # Returns True if any fix was applied.
    applied = False

    # Fix common macro redefinition errors (e.g. luatexja defines \mc).
    m = _CMD_ALREADY_DEFINED_WITH_PATH_RE.search(logs_text)
    if m:
        rel = m.group("path")
        cmd = m.group("cmd")
        if _fix_command_already_defined(work_dir, rel, cmd):
            applied = True

    # Fallback: if we didn't get a path, try to extract command name and patch main.
    if not applied:
        m2 = _CMD_ALREADY_DEFINED_RE.search(logs_text)
        if m2:
            cmd = m2.group(1)
            if _fix_command_already_defined(work_dir, main_rel, cmd):
                applied = True

    return applied


def _local_environment():
    env = os.environ.copy()
    # The desktop app may have started before MacTeX updated shell paths.
    env["PATH"] = env.get("PATH", os.defpath) + os.pathsep + "/Library/TeX/texbin"
    return env


def _require_tool(name, env):
    tool = shutil.which(name, path=env["PATH"])
    if not tool:
        raise RuntimeError(
            f"Missing local tool: {name}. Install TeX Live (macOS: "
            "brew install --cask mactex-no-gui), or add its bin directory to PATH."
        )
    return tool


def _stage_project(work_dir, destination):
    def ignore(directory, names):
        return [name for name in names if name in {
            _BUILD_DIR, ".git", "__pycache__", "__MACOSX", *_SKIP_FILENAMES
        } or name.lower().endswith(_BUILD_ARTIFACT_EXTS)]

    # Preserve ALL PDF assets: no heuristic filtering of figures.
    shutil.copytree(work_dir, destination, ignore=ignore)


def _bibliography_option(project, main_rel):
    """Do not overwrite a prebuilt biblatex .bbl when its .bib is unavailable."""
    sources = _collect_source_texts(project)
    text = _uncomment("\n".join(t for p, t in sources.items() if p.endswith(".tex")))
    if "biblatex" in _packages(text) or "\\addbibresource" in text:
        names = re.findall(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}", text)
        bbl = _find_prebuilt_bbl(project, main_rel)
        if bbl and any(not (Path(project) / name).is_file() for name in names):
            # latexmk writes job files at the project root, even for a nested main.
            target = Path(project) / (Path(main_rel).stem + ".bbl")
            source = Path(project) / bbl
            if source != target:
                shutil.copy2(source, target)
            return "-bibtex-"
    return "-bibtex-cond"


def _run_latexmk(command, cwd, env, log_path, timeout=_COMPILE_TIMEOUT):
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"cwd: {cwd}\ncommand: {command!r}\n\n")
        log.flush()
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise


def _validate_pdf(pdf_path, engine_log):
    if not pdf_path.is_file():
        raise RuntimeError("latexmk did not produce a PDF")
    with pdf_path.open("rb") as pdf:
        if pdf.read(5) != b"%PDF-":
            raise RuntimeError("Compiler output is not a PDF")
        pdf.seek(max(0, pdf_path.stat().st_size - 1024))
        if b"%%EOF" not in pdf.read():
            raise RuntimeError("Compiler output is an incomplete PDF")
    if not engine_log.is_file():
        raise RuntimeError("Missing final LaTeX log; cannot validate references")
    text = _read_text(engine_log)
    if _UNRESOLVED_LOG_RE.search(text):
        raise RuntimeError("Unresolved citations/references in final LaTeX log")
    if "Missing character:" in text:
        raise RuntimeError("Missing glyphs in final LaTeX log; check the selected fonts")


def _publish_pdf(source, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Same-filesystem atomic replacement keeps an existing PDF intact on failure.
    fd, pending = tempfile.mkstemp(prefix=".arxiv-pdf-", suffix=".pdf", dir=output.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, pending)
        os.replace(pending, output)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def compile_local(work_dir, main_tex, output_path):
    work_dir, main_rel = _main_tex_relative(work_dir, main_tex)
    output_path = _resolve_output_pdf(output_path, main_rel)
    if Path(output_path).suffix.lower() != ".pdf":
        raise ValueError("output_pdf_path must end in .pdf (or be a directory)")
    env = _local_environment()
    latexmk = _require_tool("latexmk", env)
    compiler = _detect_compiler(work_dir, main_rel)
    _require_tool(compiler, env)

    build_parent = Path(work_dir) / _BUILD_DIR
    build_parent.mkdir(exist_ok=True)
    # Keep first-run font caches writable in sandboxed desktop sessions, too.
    env.setdefault("TEXMFVAR", str(build_parent / "texmf-var"))
    env.setdefault("TEXMFCACHE", env["TEXMFVAR"])
    build = Path(tempfile.mkdtemp(prefix="run-", dir=build_parent))
    print(f"Build directory: {build}", file=sys.stderr, flush=True)
    for attempt in range(1, 4):
        _inline_prebuilt_bbl(work_dir, main_rel)
        _ensure_cjk_support(work_dir, main_rel)
        _preflight_comment_inputenc_fontenc(work_dir, main_rel)
        project = build / f"attempt-{attempt}"
        _stage_project(work_dir, project)
        pdf = project / (Path(main_rel).stem + ".pdf")
        # Copied historical output must never be mistaken for a fresh build.
        if pdf.exists():
            pdf.unlink()
        log = build / f"attempt-{attempt}.log"
        engine_log = project / (Path(main_rel).stem + ".log")
        command = [
            latexmk, "-norc", f"-{compiler}", "-interaction=nonstopmode",
            "-halt-on-error", "-file-line-error", "-no-shell-escape",
            _bibliography_option(str(project), main_rel), "./" + main_rel,
        ]
        print(f"Local {compiler}, attempt {attempt}/3; log: {log}", file=sys.stderr, flush=True)
        try:
            code = _run_latexmk(command, str(project), env, str(log))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Compilation timed out after {_COMPILE_TIMEOUT}s. Log: {log}") from None
        logs_text = _read_text(log).replace(str(project) + os.sep, "./")
        if code == 0:
            try:
                _validate_pdf(pdf, engine_log)
            except RuntimeError as error:
                raise RuntimeError(f"{error}. Logs: {log}, {engine_log}") from error
            _publish_pdf(pdf, output_path)
            print(f"✅ Wrote PDF: {output_path}")
            return True
        if attempt < 3 and _try_fix_from_logs(work_dir, main_rel, logs_text):
            continue
        print(logs_text[-8000:], file=sys.stderr)
        raise RuntimeError(f"Compilation failed (exit {code}, attempt {attempt}/3). Full log: {log}")
    return False


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python compile.py <work_dir> <main_tex> <output_pdf_path>", file=sys.stderr)
        sys.exit(2)
    try:
        compile_local(sys.argv[1], sys.argv[2], sys.argv[3])
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Compilation failed: {error}", file=sys.stderr)
        sys.exit(1)
