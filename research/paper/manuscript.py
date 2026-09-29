from __future__ import annotations

import json
import io
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
import httpx

from research.paper.evidence import collect_evidence, manuscript_prompt

def tex(value):
    mapping = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    mapping.update({'±': r'\ensuremath{\pm}', '×': r'\ensuremath{\times}', '−': '-',
                    '≤': r'\ensuremath{\leq}', '≥': r'\ensuremath{\geq}', '≈': r'\ensuremath{\approx}',
                    '≠': r'\ensuremath{\ne}', '∞': r'\ensuremath{\infty}', '\u00a0': ' ',
                    '′': r'\ensuremath{^{\prime}}', '″': r'\ensuremath{^{\prime\prime}}',
                    '²': r'\ensuremath{^{2}}', '³': r'\ensuremath{^{3}}',
                    '’': "'", '‘': '`', '“': '``', '”': "''"})
    for symbol, command in {'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'θ': 'theta',
                            'λ': 'lambda', 'μ': 'mu', 'π': 'pi', 'ρ': 'rho', 'σ': 'sigma',
                            'τ': 'tau', 'φ': 'phi', 'χ': 'chi', 'Δ': 'Delta', 'Σ': 'Sigma'}.items():
        mapping[symbol] = '\\ensuremath{\\' + command + '}'
    return "".join(mapping.get(c, c) for c in str(value))


def _macro_key(*parts):
    # TeX command names contain letters only, including numerical indices.
    digits = dict(zip("0123456789", ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]))
    value = "".join(str(p).title().replace("_", "") for p in parts)
    return "Result" + "".join(digits.get(c, c) for c in value if c.isalnum())


def generate_paper(research_dir, output_dir, title=None, template="article", *, evidence=None, draft=None, layout=None):
    """Render supplied authored/model prose against actual completed-run evidence.

    There is no topic-specific template or generated-result fallback. Callers
    record the real provider response separately when the draft was model-written.
    """
    from research.paper.evidence import write_manuscript
    if evidence is None or draft is None:
        raise ValueError('Generic paper generation requires actual run evidence and a supplied draft; connect a model or provide authored prose')
    return write_manuscript(output_dir, evidence, draft, title, template, layout)


def apply_template(directory, template="article"):
    """Apply an optional official venue template without implying submission.

    Only the conference's own .sty/.bst assets are fetched. natbib/fancyhdr come
    from the user's installed TeX distribution, preserving upstream packaging.
    """
    directory = Path(directory)
    source_path = directory / "paper.tex"
    source = source_path.read_text()
    if template == "article":
        source = source.replace(r"\usepackage[T1]{fontenc}" + "\n", "")
        source = source.replace(r"\documentclass{article}" + "\n" + r"\usepackage{iclr2027_conference,times}", r"\documentclass[11pt]{article}" + "\n" + r"\usepackage[margin=1in]{geometry}")
        source = source.replace(r"\bibliographystyle{iclr2027_conference}", r"\bibliographystyle{plainnat}")
        source = source.replace("\n" + r"\lhead{Research draft -- ICLR 2027 format}", "")
        info = {"template": "article", "submission_status": "research_draft"}
    elif template == "iclr2027":
        url = "https://media.iclr.cc/Conferences/ICLR2027/iclr-2027-style-files.zip"
        filenames = ["iclr2027_conference.sty", "iclr2027_conference.bst"]
        if not all((directory / name).exists() for name in filenames):
            response = httpx.get(url, timeout=45, follow_redirects=True)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                for name in filenames:
                    (directory / name).write_bytes(archive.read("iclr2027/" + name))
        source = source.replace(r"\documentclass[11pt]{article}", r"\documentclass{article}" + "\n" + r"\usepackage[T1]{fontenc}" + "\n" + r"\usepackage{iclr2027_conference,times}")
        source = source.replace(r"\usepackage[margin=1in]{geometry}" + "\n", "")
        source = source.replace(r"\setlength{\parskip}{0.35em}" + "\n", "")
        source = source.replace(r"\bibliographystyle{plainnat}", r"\bibliographystyle{iclr2027_conference}")
        if r"\lhead{Research draft -- ICLR 2027 format}" not in source:
            source = source.replace(r"\maketitle", r"\maketitle" + "\n" + r"\lhead{Research draft -- ICLR 2027 format}")
        # Official style also embeds a submission assertion beneath the author.
        # Patch it in the user's document; preserve the downloaded style verbatim.
        author_patch = "\n".join([r"\usepackage{etoolbox}", r"\makeatletter", r"\patchcmd{\@maketitle}{Paper under double-blind review}{Reproducible empirical draft}{}{}", r"\makeatother"])
        if "Reproducible empirical draft}{}{}" not in source:
            source = source.replace(r"\begin{document}", author_patch + "\n" + r"\begin{document}")
        source = source.replace(r"\section*{Data, code, and AI-use statement}", r"\section*{Reproducibility statement}" + "\n" + "Section 4 specifies data preparation and shared splits. Appendices provide ablations, computation, and the statistical contract. The source package supplies code, configuration, split identities, raw predictions, and metric bindings.\n" + r"\section*{Ethics statement}" + "\n" + "The study uses existing public benchmarks and collects no new human-participant observations. Aggregate scores do not establish fairness or suitability for consequential decisions.\n" + r"\section*{AI use statement}")
        info = {"template": "iclr2027", "source_url": url, "guidelines_url": "https://iclr.cc/Conferences/2027/AuthorGuidelines", "ai_policy_url": "https://iclr.cc/Conferences/2027/AIPolicyForAuthors", "submission_status": "research_draft", "assets": filenames, "license_note": "Official conference assets downloaded for the requested author-template use; original notices preserved. Third-party TeX dependencies use the installed distribution."}
    else:
        raise ValueError("Supported templates are article and iclr2027")
    source_path.write_text(source)
    (directory / "template.json").write_text(json.dumps(info, indent=2))
    return info


def compile_paper(directory, source_name="paper.tex", timeout=180):
    from research.paper.layout import compile_preflight
    directory = Path(directory).resolve()
    source = (directory / source_name).resolve()
    if source.parent != directory or source.suffix != ".tex" or not source.is_file():
        raise ValueError("Compile a .tex source in the paper directory")
    tectonic, pdflatex = shutil.which("tectonic"), shutil.which("pdflatex")
    logs = []
    if tectonic:
        commands = [[tectonic, "--keep-logs", "--keep-intermediates", "--outdir", str(directory), source.name]]
    elif pdflatex:
        commands = [[pdflatex, "-no-shell-escape", "-interaction=nonstopmode", "-file-line-error", source.name]]
        if "\\bibliography" in source.read_text():
            bibtex_binary = shutil.which("bibtex")
            if not bibtex_binary:
                raise RuntimeError("BibTeX is required for this manuscript")
            commands.extend([[bibtex_binary, source.stem], commands[0], commands[0]])
        else:
            # Cross-reference numbers need the auxiliary file from the first pass.
            commands.append(commands[0])
    else:
        report = compile_preflight(directory, source_name, False, '', actual_compilation=False)
        return {"status": "unavailable", "pdf_path": None, "log": "No tectonic or pdflatex executable found", "errors": [{"message": "Install tectonic or a TeX distribution"}],
                "preflight": report, "preflight_path": str(directory / 'layout_preflight.json')}
    # Remove an old PDF so a failed compile cannot be mistaken for new output.
    pdf = directory / (source.stem + ".pdf")
    pdf.unlink(missing_ok=True)
    exit_code = 0
    for command in commands:
        try:
            process = subprocess.run(command, cwd=directory, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            logs.append(process.stdout)
            exit_code = process.returncode
            if exit_code:
                break
        except subprocess.TimeoutExpired as error:
            logs.append(f"Compilation timed out after {timeout}s\n" + str(error.stdout or ""))
            exit_code = 124
            break
    log = "\n".join(logs)
    (directory / "compile.log").write_text(log)
    errors = [{"line": int(match.group(1)), "message": match.group(2).strip()} for match in re.finditer(r"(?:\.tex:|^l\.)(\d+)[: ]\s*(.*)", log, re.M)]
    success = exit_code == 0 and pdf.is_file()
    report = compile_preflight(directory, source_name, success, log)
    return {"status": "completed" if success else "failed", "exit_code": exit_code, "pdf_path": str(pdf) if success else None, "log": log, "log_path": str(directory / "compile.log"), "errors": errors,
            "preflight": report, "preflight_path": str(directory / 'layout_preflight.json')}


def check_paper(directory, source_name="paper.tex"):
    from research.paper.evidence import resolve_pointer
    directory = Path(directory)
    source = (directory / source_name).read_text()
    issues = []
    for match in re.finditer(r"@@[^@]+@@|TODO|TBD|FIXME", source):
        issues.append({"code": "placeholder", "line": source[:match.start()].count("\n") + 1, "message": match.group()})
    bibliography = (directory / "references.bib").read_text() if (directory / "references.bib").exists() else ""
    keys = set(re.findall(r"@\w+\s*\{\s*([^,]+),", bibliography))
    for match in re.finditer(r"\\cite\w*\{([^}]+)\}", source):
        for key in match.group(1).split(","):
            if key.strip() not in keys:
                issues.append({"code": "missing_citation", "message": key.strip()})
    for figure in re.findall(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}", source):
        if not (directory / figure).is_file():
            issues.append({"code": "missing_figure", "message": figure})
    if (directory / "bindings.json").is_file():
        macros_path = directory / 'results_macros.tex'
        macro_source = macros_path.read_text() if macros_path.is_file() else ''
        for binding in json.loads((directory / "bindings.json").read_text()):
            try:
                metric_file = (directory / binding['file']).resolve()
                if not metric_file.is_relative_to(directory.resolve()):
                    raise ValueError('Metric file must be within the paper bundle')
                value = resolve_pointer(json.loads(metric_file.read_text()), binding['pointer'])
                if value != binding["value"]:
                    issues.append({"code": "stale_metric", "message": binding["macro"], "expected": value, "bound": binding["value"]})
                if 'display' in binding:
                    literal = '\\newcommand{\\' + binding['macro'] + '}{' + binding['display'] + '}'
                    if literal not in macro_source:
                        issues.append({'code': 'stale_macro', 'message': binding['macro']})
            except (KeyError, IndexError, OSError, TypeError, ValueError):
                issues.append({"code": "missing_metric", "message": binding["macro"]})
    return {"issues": issues, "checked": ["placeholders", "citation_keys", "figure_files", "bound_metrics"], "claim_verification": "numerical binding checks only; not an automated scientific endorsement"}


def export_paper(directory, target):
    directory = Path(directory)
    target = Path(target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in directory.rglob("*"):
            if path.is_file() and path.resolve() != target.resolve() and path.suffix not in {".aux", ".blg", ".log"}:
                archive.write(path, path.relative_to(directory))
    return str(target)
