"""The .breviaire project file: schema, defaults, load/save.

No directory/gabc_root concept here — a project file is the whole document.
Item types: text (inline contents), gabc (file-linked), myr (file-linked).

File-linked items share one convention (generalised at user direction,
2026-07-09): `file_path` is the tracked file whose content enters the
export (.gabc, .ly, later images), and the optional `source_path` is the
upstream original it was derived from (.myr for polyphony; a drawing
master for future image items). When both exist and the source is newer
on disk, `file_status` flags the item stale — a hint to re-import, never
an automatic one (mtime isn't proof of visual change, and re-importing
clobbers hand-edits).
"""

import json
import os

SCHEMA_VERSION = 1

DEFAULT_PAGE = {
    "size": "a5paper",
    "margin": "1.5cm",
    "gutter": "0.5cm",
}

# Named text styles a text item's `key` can reference. Each value is the
# BODY of a TeX macro with numbered parameters #1..#N (TeX's own macro
# syntax — max 9, a hard TeX limit). At export time each style becomes a
# real \newcommand in the preamble ("all the tex defs first, then the
# body") and each text item becomes a readable macro call whose arguments
# come from the item's `contents` list, e.g. \styleheader{Office des
# Vêpres}. Multi-column layouts are styles too, not page settings — a
# style body may open/close a multicols environment (multicol is always
# loaded). Live preview (index.html) substitutes #N directly instead of
# defining macros (LaTeX.js has no \newcommand support) — same rendering,
# different mechanism. Three things are deliberately NOT reflected in that
# preview, documented in the Help modal rather than silently faked: colour
# (LaTeX.js's \color/\textcolor are no-op stubs upstream), exact font size
# (\fontsize{}{} isn't recognised at all — an unknown-macro parse error,
# not a no-op — so it's stripped before parsing rather than substituted
# with a different size), and font face (no fontspec support, so real
# export fonts like Adobe Garamond Pro never show here).
DEFAULT_STYLES = {
    "header": r"{\centering\bfseries\fontsize{24pt}{28pt}\selectfont #1\par}",
    "subtitle": r"{\centering\itshape\fontsize{14pt}{17pt}\selectfont #1\par}",
    "body": r"{\fontsize{11pt}{13pt}\selectfont #1\par}",
    "rubric": r"{\raggedright\itshape\color{red}\fontsize{11pt}{13pt}\selectfont #1\par}",
    "colonnes": r"\begin{multicols}{2}\fontsize{11pt}{13pt}\selectfont #1\par\end{multicols}",
}

# Music engraving defaults shared by every gabc/ly item ("define shared
# settings once" — \grechangestaffsize + lyluatex's staffsize option).
DEFAULT_MUSIC = {
    # gabc staff size (gregoriotex \grechangestaffsize). Historically drove
    # both music surfaces; ly now has its own optional size below.
    "staffsize": 16,
    # lilypond staff size (lyluatex). 0/None = inherit `staffsize`, so the
    # default stays uniform — set only to size polyphony differently.
    "ly_staffsize": 0,
}

# French punctuation spacing strategy for the export (see exporter.py):
# "frpunct" embeds app/defaults/frpunct.lua (CI-validated default);
# "babel" uses \usepackage[french]{babel} instead (same upstream code,
# maintained, adds French hyphenation — switch once a babel-inclusive CI
# smoke run is green).
# The app-bundled default text font (OFL EB Garamond, in app/fonts/). Single
# source of truth: it is both the schema default in DEFAULT_TYPOGRAPHY below
# and — via exporter.build_tex — the fallback used when text_font is explicitly
# emptied, so "no font" renders as the house Garamond, never the engine's
# surprise Latin Modern. The two are deliberately the same face.
DEFAULT_TEXT_FONT = "EB Garamond"

DEFAULT_TYPOGRAPHY = {
    "french_spacing": "frpunct",
    # Per-surface fonts/sizes — uniform by DEFAULT (gabc/ly inherit the base
    # text font), with optional independent overrides. A font name must be
    # visible to BOTH luaotfload (TeX) and fontconfig/Pango (LilyPond) — a
    # system-installed family satisfies both. See exporter/build_tex, the
    # /api/validate-font check, and TODO Phase 6.
    # Default is the app-bundled EB Garamond (OFL) so a new bréviaire gets the
    # Garamond house look out of the box and renders identically on any machine
    # (the font ships with the app — see exporter._bundled_fonts_dir). Fully
    # overridable; an emptied value falls back to this same bundled face at
    # export (exporter uses DEFAULT_TEXT_FONT), not to the engine's Latin
    # Modern — the default and the "no font" case are one and the same.
    "text_font": DEFAULT_TEXT_FONT,
    "text_size": 0,     # base body size in pt. 0 = engine default (10pt).
    "gabc_font": "",     # '' = inherit text_font (gabc lyrics uniform w/ body)
    "ly_font": "",       # '' = inherit text_font (lilypond lyrics uniform)
}

# image (Phase 5b): a general-purpose illustration — engraving, scanned
# score, ornament. {type, file_path (pdf/png/jpg), source_path? (editable
# original, any format), width? (fraction of \linewidth, default 1),
# crop? ({left, top, right, bottom} as 0–1 fractions cropped from each
# side — never applied to the file itself, only at render/export)}.
ITEM_TYPES = {"text", "gabc", "myr", "image"}


def new_project():
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [],
        "defs": {
            "page": dict(DEFAULT_PAGE),
            "styles": dict(DEFAULT_STYLES),
            "music": dict(DEFAULT_MUSIC),
            "typography": dict(DEFAULT_TYPOGRAPHY),
        },
    }


class ProjectError(Exception):
    pass


def _normalize_item(item):
    """Load-time shim: early myr items used myr_path/ly_path before the
    generic file_path/source_path convention landed (Phase 4)."""
    if isinstance(item, dict) and item.get("type") == "myr":
        if "file_path" not in item and "ly_path" in item:
            item["file_path"] = item.pop("ly_path")
        if "source_path" not in item and "myr_path" in item:
            item["source_path"] = item.pop("myr_path")
    return item


def validate_project(data):
    if not isinstance(data, dict):
        raise ProjectError("Le fichier de projet doit être un objet JSON")
    if data.get("schema_version") is None:
        raise ProjectError("Champ schema_version manquant")
    if data["schema_version"] > SCHEMA_VERSION:
        raise ProjectError(
            f"Ce projet a été créé avec une version plus récente "
            f"({data['schema_version']}) que celle prise en charge ici "
            f"({SCHEMA_VERSION})"
        )
    items = data.get("items")
    if not isinstance(items, list):
        raise ProjectError("Champ items manquant ou invalide")
    for i, item in enumerate(items):
        _normalize_item(item)
        if not isinstance(item, dict) or item.get("type") not in ITEM_TYPES:
            raise ProjectError(f"Item {i} : type invalide ou manquant")
        if item["type"] == "text" and not isinstance(item.get("contents"), list):
            raise ProjectError(
                f"Item {i} : contents doit être une liste "
                f"(une entrée = texte normal, 2+ = colonnes parallèles)"
            )
    if not isinstance(data.get("defs"), dict):
        raise ProjectError("Champ defs manquant ou invalide")
    return data


def file_status(data):
    """Per-item existence + staleness check for linked files, returned as a
    parallel list — never merged into the persisted item data itself.

    Generic across item types: any file-linked item is checked on its
    `file_path` and (if set) `source_path`; `stale` is set when both exist
    and the source is newer on disk than the derived file.
    """
    status = []
    for item in data.get('items', []):
        paths = {}
        if item.get('type') in ('gabc', 'myr', 'image'):
            paths['file_path'] = item.get('file_path')
            if item.get('source_path'):
                paths['source_path'] = item.get('source_path')
        missing = [k for k, p in paths.items() if p and not os.path.isfile(p)]
        entry = {'missing': missing}
        src, derived = item.get('source_path'), item.get('file_path')
        if (src and derived and os.path.isfile(src) and os.path.isfile(derived)):
            try:
                entry['stale'] = os.path.getmtime(src) > os.path.getmtime(derived)
            except OSError:
                pass
        status.append(entry)
    return status


def load_project(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return validate_project(data)


def save_project(path, data):
    validate_project(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
