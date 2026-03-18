"""Ad-hoc search and update logic driven by a refs markdown file.

Each line in the refs file is one or more entries of the form:
    Name (type1/type2/...)
    Name1 (type) / Name2 (type)

Type annotation determines the search strategy:
  - person types (artist, musician, director, etc.) → search works by them
  - book   → find that specific artifact via title search
  - movie  → find that specific film via title search
  - magazine / publication → find all issues
  - movement → broad topic / movement search
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console

console = Console()

# ── type buckets ──────────────────────────────────────────────────────────────

MUSICIAN_TYPES = {"musician", "musicians"}
DIRECTOR_TYPES = {"director", "directors", "actor", "actors"}
ARTIST_TYPES   = {"artist", "artists", "photographer", "photographers",
                  "photgrapher", "photgraper", "architect", "architects",
                  "designer", "designers", "painter", "painters"}
WRITER_TYPES   = {"writer", "writers", "author", "authors"}
FIGURE_TYPES   = {"figure", "figures"}
PERSON_TYPES   = MUSICIAN_TYPES | DIRECTOR_TYPES | ARTIST_TYPES | WRITER_TYPES | FIGURE_TYPES

MOVEMENT_TYPES  = {"movement", "movements"}
BOOK_TYPES      = {"book", "books"}
MOVIE_TYPES     = {"movie", "movies", "film", "films"}
MAGAZINE_TYPES  = {"magazine", "magazines", "publication", "publications",
                   "publishing house", "zine", "zines"}


# ── parsing ───────────────────────────────────────────────────────────────────

def parse_refs_file(filepath: Path) -> list[dict]:
    """Parse a refs markdown file into a flat list of entries.

    Each entry dict has:
        name  – display name / search name
        types – list of lowercase type strings (may be empty)
        raw   – original source line
    """
    entries = []
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.extend(_parse_line(line))
    return entries


def _parse_line(line: str) -> list[dict]:
    """Extract one or more (name, types) pairs from a single line.

    Works by locating every (...) group in order and treating the text
    before each group as the name for that entry.

    Examples
    --------
    "Alberto Giacometti (figure/artist)"
        → [{"name": "Alberto Giacometti", "types": ["figure", "artist"]}]

    "Richard Kern (director/photographer) / XXgirls (book)"
        → [{"name": "Richard Kern",  "types": ["director", "photographer"]},
           {"name": "XXgirls",       "types": ["book"]}]

    "Futurism / Futurist architecture (movement)"
        → [{"name": "Futurism / Futurist architecture", "types": ["movement"]}]

    "Henry Miller"  (no parens)
        → [{"name": "Henry Miller", "types": []}]
    """
    type_pattern = re.compile(r"\(([^)]+)\)")
    type_matches = list(type_pattern.finditer(line))

    if not type_matches:
        return [{"name": line.strip(), "types": [], "raw": line}]

    entries = []
    for i, match in enumerate(type_matches):
        # Text segment preceding this (type) group
        start = type_matches[i - 1].end() if i > 0 else 0
        name = line[start : match.start()].strip().strip("/").strip()

        types = [t.strip().lower() for t in match.group(1).split("/")]

        if name:
            entries.append({"name": name, "types": types, "raw": line})

    return entries


# ── classification ────────────────────────────────────────────────────────────

def classify_entry(entry: dict) -> str:
    """Return one of: person | book | movie | magazine | movement | unknown."""
    types = set(entry.get("types", []))

    if types & MOVIE_TYPES:
        return "movie"
    if types & BOOK_TYPES:
        return "book"
    if types & MAGAZINE_TYPES:
        return "magazine"
    if types & MOVEMENT_TYPES:
        return "movement"
    if types & PERSON_TYPES:
        return "person"
    return "unknown"


def get_category_and_mediatype(entry: dict, classification: str) -> tuple[str, list[str]]:
    """Map an entry to (category_name, [mediatypes]) for categories.yaml."""
    types = set(entry.get("types", []))

    if classification == "movie":
        return "film_and_cinema", ["movies"]
    if classification == "book":
        return "literature", ["texts"]
    if classification == "magazine":
        return "publications", ["texts"]
    if classification == "movement":
        return "visual_art_and_artists", ["texts"]

    if classification in ("person", "unknown"):
        if types & MUSICIAN_TYPES:
            return "music_and_sound", ["audio"]
        if types & DIRECTOR_TYPES:
            return "film_and_cinema", ["movies", "texts"]
        if types & ARTIST_TYPES:
            return "visual_art_and_artists", ["texts"]
        if types & WRITER_TYPES:
            return "literature", ["texts"]
        if types & FIGURE_TYPES:
            return "figures", ["texts"]

    return "figures", ["texts"]


# ── term building ─────────────────────────────────────────────────────────────

def build_term_dict(entry: dict, classification: str) -> dict:
    """Build the term dict that goes into categories.yaml / analyze_category."""
    name = entry["name"]

    if classification == "book":
        return {"name": name, "search_term": f'title:("{name}")', "mediatype": ["texts"]}

    if classification == "movie":
        return {"name": name, "search_term": f'title:("{name}")', "mediatype": ["movies"]}

    # person / magazine / movement / unknown → plain name search
    return {"name": name}


def get_max_results(classification: str, default: int = 50, magazine_max: int = 200) -> int:
    """Return an appropriate max_results value for the entry type."""
    if classification == "magazine":
        return magazine_max
    if classification in ("book", "movie"):
        return 10   # we only want the specific artifact
    return default


# ── categories.yaml helpers ───────────────────────────────────────────────────

def is_already_in_categories(name: str, categories: dict) -> bool:
    """Return True if *name* (case-insensitive) exists in any category's terms."""
    name_lower = name.lower()
    for cat_config in categories.values():
        for term in cat_config.get("terms", []):
            term_name = term if isinstance(term, str) else term.get("name", "")
            if term_name.lower() == name_lower:
                return True
    return False


def update_categories_yaml(
    categories_path: Path,
    entries_with_class: list[tuple[dict, str]],
    dry_run: bool = False,
) -> dict:
    """Add new entries to categories.yaml.

    Returns stats dict with 'added' (list of (name, cat)) and 'skipped' (list of names).
    """
    with open(categories_path) as f:
        categories = yaml.safe_load(f)

    stats: dict = {"added": [], "skipped": []}

    # Ensure publications category exists when needed
    needs_publications = any(cls == "magazine" for _, cls in entries_with_class)
    if needs_publications and "publications" not in categories:
        categories["publications"] = {
            "description": "Magazines, zines and publishing houses",
            "mediatype": ["texts"],
            "terms": [],
        }

    for entry, classification in entries_with_class:
        name = entry["name"]

        if is_already_in_categories(name, categories):
            stats["skipped"].append(name)
            continue

        cat_name, mediatypes = get_category_and_mediatype(entry, classification)
        term_dict = build_term_dict(entry, classification)

        # Ensure target category exists
        if cat_name not in categories:
            categories[cat_name] = {
                "description": "Auto-generated",
                "mediatype": mediatypes,
                "terms": [],
            }

        categories[cat_name].setdefault("terms", []).append(term_dict)
        stats["added"].append((name, cat_name))

    if not dry_run:
        with open(categories_path, "w", encoding="utf-8") as f:
            yaml.dump(categories, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return stats
