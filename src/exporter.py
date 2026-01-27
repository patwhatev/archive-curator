"""Export search results to various formats."""
from __future__ import annotations
import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from rich.console import Console

console = Console()

# Maximum items to display per mediatype (None = no limit)
MEDIATYPE_LIMITS: Dict[str, Optional[int]] = {
    "movies": 10,
}


def _normalize_title(title: str) -> str:
    """Normalize a title by removing special characters and lowercasing."""
    # Remove all non-alphanumeric characters except spaces
    normalized = re.sub(r'[^a-zA-Z0-9\s]', '', title)
    # Collapse multiple spaces and lowercase
    normalized = re.sub(r'\s+', ' ', normalized).strip().lower()
    return normalized


def _titles_are_similar(title1: str, title2: str, threshold: float = 0.98) -> bool:
    """Check if two titles are similar using fuzzy matching."""
    norm1 = _normalize_title(title1)
    norm2 = _normalize_title(title2)

    # Quick exact match check
    if norm1 == norm2:
        return True

    # Fuzzy match
    ratio = SequenceMatcher(None, norm1, norm2).ratio()
    return ratio >= threshold


def _deduplicate_items(items: list) -> list:
    """Remove duplicate items by identifier and similar titles, keeping highest confidence score."""
    # First pass: dedupe by identifier
    by_identifier = {}
    for item in items:
        if item.identifier not in by_identifier or item.confidence.score > by_identifier[item.identifier].confidence.score:
            by_identifier[item.identifier] = item

    # Second pass: dedupe by similar titles
    # Sort by confidence score descending so we keep higher-scored items
    sorted_items = sorted(by_identifier.values(), key=lambda x: x.confidence.score, reverse=True)

    unique_items = []
    seen_titles = []

    for item in sorted_items:
        # Check if this title is too similar to any we've already kept
        is_duplicate = False
        for seen_title in seen_titles:
            if _titles_are_similar(item.title, seen_title):
                is_duplicate = True
                break

        if not is_duplicate:
            unique_items.append(item)
            seen_titles.append(item.title)

    return unique_items


def _apply_mediatype_limits(items: list) -> list:
    """Apply per-mediatype limits to items."""
    # Group by mediatype
    by_mediatype: Dict[str, list] = {}
    for item in items:
        mt = item.mediatype
        if mt not in by_mediatype:
            by_mediatype[mt] = []
        by_mediatype[mt].append(item)

    # Apply limits and flatten
    result = []
    for mediatype, mediatype_items in by_mediatype.items():
        limit = MEDIATYPE_LIMITS.get(mediatype)
        if limit is not None:
            # Sort by confidence and take top N
            mediatype_items = sorted(mediatype_items, key=lambda x: x.confidence.score, reverse=True)[:limit]
        result.extend(mediatype_items)

    return result


CSV_FIELDNAMES = [
    "category",
    "search_term",
    "title",
    "identifier",
    "url",
    "mediatype",
    "confidence_score",
    "creator",
    "publisher",
    "page_count",
]


def load_existing_csv(path: Path) -> list[dict]:
    """Load existing CSV data as list of dicts."""
    if not path.exists():
        return []

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def export_to_csv(items: list, output_path: Path, include_failed: bool = False, append: bool = False) -> int:
    """Export analyzed items to CSV.

    Args:
        items: List of AnalyzedItem objects
        output_path: Path to write CSV file
        include_failed: Include items that didn't pass confidence threshold
        append: If True, merge with existing CSV data instead of overwriting

    Returns:
        Number of items exported
    """
    filtered = items if include_failed else [i for i in items if i.confidence.passes]
    filtered = _deduplicate_items(filtered)
    filtered = _apply_mediatype_limits(filtered)

    # Convert new items to dicts
    new_rows = []
    for item in filtered:
        new_rows.append({
            "category": item.category or "",
            "search_term": item.search_term,
            "title": item.title,
            "identifier": item.identifier,
            "url": item.url,
            "mediatype": item.mediatype,
            "confidence_score": item.confidence.score,
            "creator": item.creator or "",
            "publisher": item.publisher or "",
            "page_count": item.page_count or "",
        })

    # If appending, merge with existing data
    if append and output_path.exists():
        existing_rows = load_existing_csv(output_path)
        existing_ids = {row["identifier"] for row in existing_rows}

        # Add only new items (by identifier)
        added_count = 0
        for row in new_rows:
            if row["identifier"] not in existing_ids:
                existing_rows.append(row)
                existing_ids.add(row["identifier"])
                added_count += 1

        all_rows = existing_rows
        console.print(f"[green]Added {added_count} new items (skipped {len(new_rows) - added_count} duplicates)[/green]")
    else:
        all_rows = new_rows

    if not all_rows:
        console.print("[yellow]No items to export.[/yellow]")
        return 0

    # Write all data
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    console.print(f"[green]Total {len(all_rows)} items in {output_path}[/green]")
    return len(all_rows)


def get_thumbnail_url(identifier: str) -> str:
    """Get the archive.org thumbnail URL for an item."""
    return f"https://archive.org/services/img/{identifier}"


def export_to_html(
    items: list,
    output_path: Path,
    title: str = "Archive Curator",
    include_failed: bool = False
) -> int:
    """Export analyzed items to an HTML page.

    Args:
        items: List of AnalyzedItem objects
        output_path: Path to write HTML file
        title: Page title
        include_failed: Include items that didn't pass confidence threshold

    Returns:
        Number of items exported
    """
    filtered = items if include_failed else [i for i in items if i.confidence.passes]
    filtered = _deduplicate_items(filtered)
    filtered = _apply_mediatype_limits(filtered)

    if not filtered:
        console.print("[yellow]No items to export.[/yellow]")
        return 0

    # Group items by search term
    grouped = {}
    for item in filtered:
        term = item.search_term
        if term not in grouped:
            grouped[term] = []
        grouped[term].append(item)

    # Generate HTML
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @import url('https://fonts.cdnfonts.com/css/helvetica-neue-55');

        :root {{
            --bg: #000000;
            --card-bg: #000000;
            --text: #ffffff;
            --text-dim: #888888;
            --accent: #a90000;
            --border: #333333;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-weight: 400;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            padding: 3rem;
            font-size: 11pt;
            text-transform: uppercase;
        }}

        .container {{ max-width: 1400px; margin: 0 auto; }}

        header {{
            margin-bottom: 3rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border);
        }}

        h1 {{
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-weight: 700;
            font-size: 47pt;
            color: var(--text);
            margin-bottom: 0.5rem;
            letter-spacing: -0.02em;
        }}

        .meta {{
            color: var(--text-dim);
            font-size: 11pt;
        }}

        .controls {{
            margin: 1.5rem 0;
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
        }}

        button {{
            background: var(--bg);
            color: var(--text);
            border: 1px solid var(--text);
            padding: 0.5rem 1.25rem;
            cursor: pointer;
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-size: 11pt;
            font-weight: 500;
            text-transform: uppercase;
            transition: all 0.15s ease;
        }}

        button:hover {{
            background: var(--text);
            color: var(--bg);
        }}

        .category {{
            margin-bottom: 3rem;
        }}

        .category h2 {{
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-weight: 700;
            font-size: 33pt;
            color: var(--text);
            margin-bottom: 1.5rem;
            padding-bottom: 0.75rem;
            border-bottom: 1px solid var(--border);
            letter-spacing: -0.01em;
        }}

        .items {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1.5rem;
        }}

        .item {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            padding: 1rem;
            transition: border-color 0.15s ease;
        }}

        .item:hover {{
            border-color: var(--accent);
        }}

        .item-thumbnail {{
            width: 100%;
            height: 200px;
            object-fit: cover;
            margin-bottom: 0.75rem;
            background: #111111;
        }}

        .item-title {{
            font-weight: 700;
            font-size: 11pt;
            margin-bottom: 0.75rem;
            line-height: 1.3;
        }}

        .item-title a {{
            color: var(--text);
            text-decoration: none;
        }}

        .item-title a:hover {{
            color: var(--accent);
        }}

        .item-meta {{
            font-size: 11pt;
            color: var(--text-dim);
            margin-bottom: 0.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }}

        .item-meta-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .meta-label {{
            color: var(--text-dim);
        }}

        .meta-value {{
            color: var(--text);
            font-weight: 500;
        }}

        .meta-value.accent {{
            color: var(--accent);
        }}

        .item-creator {{
            font-size: 11pt;
            color: var(--text-dim);
            font-style: italic;
            margin-top: 0.5rem;
        }}

        .open-link {{
            display: inline-block;
            margin-top: 0.75rem;
            color: var(--accent);
            font-size: 11pt;
            text-decoration: none;
            font-weight: 500;
        }}

        .open-link:hover {{
            text-decoration: underline;
        }}

        footer {{
            margin-top: 4rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border);
            color: var(--text-dim);
            font-size: 11pt;
        }}

        footer a {{
            color: var(--accent);
            text-decoration: none;
        }}

        footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{title}</h1>
            <p class="meta">{timestamp} / {len(filtered)} items / {len(grouped)} artists</p>
        </header>

        <div class="controls">
            <button onclick="openCategory(event)">Open Category</button>
        </div>
"""

    for term, term_items in sorted(grouped.items()):
        html += f"""
        <section class="category" data-term="{term}">
            <h2>{term}</h2>
            <div class="items">
"""
        for item in sorted(term_items, key=lambda x: x.confidence.score, reverse=True):
            thumbnail_url = get_thumbnail_url(item.identifier)
            creator = f'<div class="item-creator">{item.creator}</div>' if item.creator else ""

            html += f"""
                <div class="item" data-url="{item.url}">
                    <a href="{item.url}" target="_blank">
                        <img class="item-thumbnail" src="{thumbnail_url}" alt="{item.title}" loading="lazy" onerror="this.style.display='none'">
                    </a>
                    <div class="item-title">
                        <a href="{item.url}" target="_blank">{item.title}</a>
                    </div>
                    <div class="item-meta">
                        <div class="item-meta-row">
                            <span class="meta-label">Confidence</span>
                            <span class="meta-value accent">{item.confidence.score}</span>
                        </div>
                        <div class="item-meta-row">
                            <span class="meta-label">Type</span>
                            <span class="meta-value">{item.mediatype}</span>
                        </div>
                    </div>
                    {creator}
                    <a href="{item.url}" target="_blank" class="open-link">View &rarr;</a>
                </div>
"""
        html += """
            </div>
        </section>
"""

    html += """
        <footer>
            <p>Generated by <a href="https://github.com/archive-curator">Archive Curator</a></p>
        </footer>
    </div>

    <script>
        function openAll() {
            const urls = [...document.querySelectorAll('.item')].map(el => el.dataset.url);
            if (confirm(`Open ${urls.length} tabs?`)) {
                urls.forEach(url => window.open(url, '_blank'));
            }
        }

        function openCategory(event) {
            const categories = [...document.querySelectorAll('.category')];
            const names = categories.map(c => c.dataset.term);
            const choice = prompt(`Enter category name:\\n${names.join('\\n')}`);
            if (choice) {
                const cat = categories.find(c => c.dataset.term.toLowerCase() === choice.toLowerCase());
                if (cat) {
                    const urls = [...cat.querySelectorAll('.item')].map(el => el.dataset.url);
                    urls.forEach(url => window.open(url, '_blank'));
                }
            }
        }

        function copyUrls() {
            const urls = [...document.querySelectorAll('.item')].map(el => el.dataset.url).join('\\n');
            navigator.clipboard.writeText(urls).then(() => alert('URLs copied to clipboard!'));
        }
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    console.print(f"[green]Exported {len(filtered)} items to {output_path}[/green]")
    return len(filtered)


def export_to_json(items: list, output_path: Path, include_failed: bool = False) -> int:
    """Export analyzed items to JSON.

    Args:
        items: List of AnalyzedItem objects
        output_path: Path to write JSON file
        include_failed: Include items that didn't pass confidence threshold

    Returns:
        Number of items exported
    """
    filtered = items if include_failed else [i for i in items if i.confidence.passes]
    filtered = _deduplicate_items(filtered)
    filtered = _apply_mediatype_limits(filtered)

    if not filtered:
        console.print("[yellow]No items to export.[/yellow]")
        return 0

    data = {
        "exported_at": datetime.now().isoformat(),
        "total_items": len(filtered),
        "items": [
            {
                "title": item.title,
                "identifier": item.identifier,
                "url": item.url,
                "mediatype": item.mediatype,
                "search_term": item.search_term,
                "confidence_score": item.confidence.score,
                "passes": item.confidence.passes,
                "creator": item.creator,
                "publisher": item.publisher,
                "page_count": item.page_count,
                "scoring_reasons": item.confidence.reasons,
            }
            for item in filtered
        ]
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    console.print(f"[green]Exported {len(filtered)} items to {output_path}[/green]")
    return len(filtered)


def generate_html_viewer(csv_filename: str, output_path: Path, title: str = "Archive Curator") -> None:
    """Generate an HTML viewer that reads from a CSV file.

    The HTML file uses JavaScript to parse the CSV and render items dynamically.
    Edit the CSV and refresh the browser to see changes.

    Args:
        csv_filename: Name of the CSV file (relative to the HTML file)
        output_path: Path to write the HTML viewer
        title: Page title
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @import url('https://fonts.cdnfonts.com/css/helvetica-neue-55');

        :root {{
            --bg: #000000;
            --sidebar-bg: #0a0a0a;
            --card-bg: #000000;
            --text: #ffffff;
            --text-dim: #888888;
            --accent: #a90000;
            --border: #333333;
            --hover-bg: #1a1a1a;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-weight: 400;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            font-size: 11pt;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }}

        /* Sidebar */
        .sidebar {{
            width: 280px;
            min-width: 280px;
            background: var(--sidebar-bg);
            border-right: 1px solid var(--border);
            overflow-y: auto;
            padding: 1.5rem 0;
        }}

        .sidebar-header {{
            padding: 0 1.5rem 1.5rem;
            border-bottom: 1px solid var(--border);
            margin-bottom: 1rem;
        }}

        .sidebar-header h1 {{
            font-size: 18pt;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: -0.02em;
        }}

        .sidebar-header .meta {{
            color: var(--text-dim);
            font-size: 10pt;
            margin-top: 0.5rem;
            text-transform: uppercase;
        }}

        /* Category accordion */
        .category {{
            border-bottom: 1px solid var(--border);
        }}

        .category-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem 1.5rem;
            cursor: pointer;
            text-transform: uppercase;
            font-weight: 700;
            font-size: 10pt;
            letter-spacing: 0.05em;
            transition: background 0.15s ease;
        }}

        .category-header:hover {{
            background: var(--hover-bg);
        }}

        .category-header .arrow {{
            display: inline-block;
            width: 0;
            height: 0;
            border-top: 4px solid transparent;
            border-bottom: 4px solid transparent;
            border-left: 6px solid var(--text);
            transition: transform 0.2s ease;
        }}

        .category.open .category-header .arrow {{
            transform: rotate(90deg);
        }}

        .category-items {{
            display: none;
            padding-bottom: 0.5rem;
        }}

        .category.open .category-items {{
            display: block;
        }}

        .artist-item {{
            padding: 0.5rem 1.5rem 0.5rem 2rem;
            cursor: pointer;
            font-size: 10pt;
            text-transform: uppercase;
            color: var(--text-dim);
            transition: all 0.15s ease;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .artist-item:hover {{
            background: var(--hover-bg);
            color: var(--text);
        }}

        .artist-item.active {{
            color: var(--accent);
            background: var(--hover-bg);
        }}

        .artist-item .count {{
            font-size: 9pt;
            opacity: 0.6;
        }}

        /* Main content */
        .main {{
            flex: 1;
            overflow-y: auto;
            padding: 2rem 3rem;
        }}

        .main-header {{
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
        }}

        .main-header h2 {{
            font-size: 28pt;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: -0.02em;
        }}

        .main-header .subtitle {{
            color: var(--text-dim);
            font-size: 11pt;
            text-transform: uppercase;
            margin-top: 0.25rem;
        }}

        .controls {{
            margin-bottom: 1.5rem;
            display: flex;
            gap: 0.75rem;
            flex-wrap: wrap;
        }}

        button {{
            background: var(--bg);
            color: var(--text);
            border: 1px solid var(--text);
            padding: 0.4rem 1rem;
            cursor: pointer;
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            font-size: 10pt;
            font-weight: 500;
            text-transform: uppercase;
            transition: all 0.15s ease;
        }}

        button:hover {{
            background: var(--text);
            color: var(--bg);
        }}

        /* Items grid */
        .items {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 1.5rem;
        }}

        .item {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            padding: 1rem;
            transition: border-color 0.15s ease;
        }}

        .item:hover {{
            border-color: var(--accent);
        }}

        .item-thumbnail {{
            width: 100%;
            height: 180px;
            object-fit: cover;
            margin-bottom: 0.75rem;
            background: #111111;
        }}

        .item-title {{
            font-weight: 700;
            font-size: 10pt;
            margin-bottom: 0.5rem;
            line-height: 1.3;
            text-transform: uppercase;
        }}

        .item-title a {{
            color: var(--text);
            text-decoration: none;
        }}

        .item-title a:hover {{
            color: var(--accent);
        }}

        .item-meta {{
            font-size: 9pt;
            color: var(--text-dim);
            text-transform: uppercase;
        }}

        .item-meta-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.25rem;
        }}

        .meta-value.accent {{
            color: var(--accent);
        }}

        .item-creator {{
            font-size: 9pt;
            color: var(--text-dim);
            font-style: italic;
            margin-top: 0.5rem;
            text-transform: none;
        }}

        .open-link {{
            display: inline-block;
            margin-top: 0.5rem;
            color: var(--accent);
            font-size: 9pt;
            text-decoration: none;
            font-weight: 500;
            text-transform: uppercase;
        }}

        .open-link:hover {{
            text-decoration: underline;
        }}

        /* Empty state */
        .empty-state {{
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-dim);
        }}

        .empty-state h3 {{
            font-size: 14pt;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
        }}

        /* Loading state */
        .loading {{
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-dim);
            text-transform: uppercase;
        }}

        /* Mobile menu toggle */
        .mobile-header {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 100;
            background: var(--bg);
            border-bottom: 1px solid var(--border);
            padding: 1rem 1.5rem;
        }}

        .mobile-header-content {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .mobile-header h1 {{
            font-size: 14pt;
            font-weight: 700;
            text-transform: uppercase;
        }}

        .menu-toggle {{
            background: none;
            border: 1px solid var(--text);
            color: var(--text);
            padding: 0.5rem 0.75rem;
            font-size: 9pt;
            text-transform: uppercase;
        }}

        .menu-toggle.active {{
            background: var(--text);
            color: var(--bg);
        }}

        /* Mobile styles */
        @media (max-width: 768px) {{
            body {{
                flex-direction: column;
            }}

            .mobile-header {{
                display: block;
            }}

            .sidebar {{
                position: fixed;
                top: 60px;
                left: 0;
                right: 0;
                bottom: 0;
                width: 100%;
                z-index: 99;
                transform: translateY(-100%);
                opacity: 0;
                transition: transform 0.3s ease, opacity 0.3s ease;
                overflow-y: auto;
                max-height: calc(100vh - 60px);
            }}

            .sidebar.open {{
                transform: translateY(0);
                opacity: 1;
            }}

            .sidebar-header {{
                display: none;
            }}

            .main {{
                margin-top: 60px;
                padding: 1.5rem;
            }}

            .main-header h2 {{
                font-size: 18pt;
            }}

            .main-header .subtitle {{
                font-size: 9pt;
            }}

            /* 2-column grid on mobile */
            .items {{
                grid-template-columns: repeat(2, 1fr);
                gap: 1rem;
            }}

            .item {{
                padding: 0.75rem;
            }}

            .item-thumbnail {{
                height: 120px;
            }}

            .item-title {{
                font-size: 9pt;
            }}

            .item-meta {{
                font-size: 8pt;
            }}

            .item-creator {{
                font-size: 8pt;
            }}

            .open-link {{
                font-size: 8pt;
            }}

            .category-header {{
                padding: 1rem 1.5rem;
            }}

            .artist-item {{
                padding: 0.75rem 1.5rem 0.75rem 2rem;
            }}
        }}

        /* Small mobile - single column */
        @media (max-width: 400px) {{
            .items {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <!-- Mobile header -->
    <div class="mobile-header">
        <div class="mobile-header-content">
            <h1>{title}</h1>
            <button class="menu-toggle" onclick="toggleMobileMenu()">Menu</button>
        </div>
    </div>

    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <h1>{title}</h1>
            <p class="meta" id="stats">Loading...</p>
        </div>
        <nav id="nav"></nav>
    </aside>

    <main class="main">
        <div class="loading" id="loading">Loading data...</div>
        <div id="content" style="display: none;">
            <header class="main-header">
                <h2 id="current-title">Select an artist</h2>
                <p class="subtitle" id="current-subtitle"></p>
            </header>
            <div class="items" id="items"></div>
        </div>
    </main>

    <script>
        const CSV_FILE = '{csv_filename}';
        let allData = [];
        let currentItems = [];

        // Mobile menu functions
        function toggleMobileMenu() {{
            const sidebar = document.getElementById('sidebar');
            const toggle = document.querySelector('.menu-toggle');
            sidebar.classList.toggle('open');
            toggle.classList.toggle('active');
            toggle.textContent = sidebar.classList.contains('open') ? 'Close' : 'Menu';
        }}

        function closeMobileMenu() {{
            const sidebar = document.getElementById('sidebar');
            const toggle = document.querySelector('.menu-toggle');
            if (window.innerWidth <= 768) {{
                sidebar.classList.remove('open');
                toggle.classList.remove('active');
                toggle.textContent = 'Menu';
            }}
        }}

        // Parse CSV
        function parseCSV(text) {{
            const lines = text.trim().split('\\n');
            const headers = parseCSVLine(lines[0]);
            const data = [];

            for (let i = 1; i < lines.length; i++) {{
                const values = parseCSVLine(lines[i]);
                const row = {{}};
                headers.forEach((h, idx) => row[h] = values[idx] || '');
                data.push(row);
            }}
            return data;
        }}

        function parseCSVLine(line) {{
            const result = [];
            let current = '';
            let inQuotes = false;

            for (let i = 0; i < line.length; i++) {{
                const char = line[i];
                if (char === '"') {{
                    inQuotes = !inQuotes;
                }} else if (char === ',' && !inQuotes) {{
                    result.push(current.trim());
                    current = '';
                }} else {{
                    current += char;
                }}
            }}
            result.push(current.trim());
            return result;
        }}

        // Build navigation
        function buildNav(data) {{
            const nav = document.getElementById('nav');
            const structure = {{}};

            // Group by category -> artist
            data.forEach(item => {{
                const cat = item.category || 'Uncategorized';
                const artist = item.search_term || 'Unknown';

                if (!structure[cat]) structure[cat] = {{}};
                if (!structure[cat][artist]) structure[cat][artist] = [];
                structure[cat][artist].push(item);
            }});

            nav.innerHTML = '';

            Object.keys(structure).sort().forEach(category => {{
                const artists = structure[category];
                const artistCount = Object.keys(artists).length;
                const itemCount = Object.values(artists).flat().length;

                const catDiv = document.createElement('div');
                catDiv.className = 'category';
                catDiv.innerHTML = `
                    <div class="category-header" onclick="toggleCategory(this)">
                        <span>${{category}} (${{itemCount}})</span>
                        <span class="arrow"></span>
                    </div>
                    <div class="category-items"></div>
                `;

                const itemsDiv = catDiv.querySelector('.category-items');
                Object.keys(artists).sort().forEach(artist => {{
                    const items = artists[artist];
                    const artistDiv = document.createElement('div');
                    artistDiv.className = 'artist-item';
                    artistDiv.innerHTML = `
                        <span>${{artist}}</span>
                        <span class="count">${{items.length}}</span>
                    `;
                    artistDiv.onclick = () => showArtist(category, artist, items);
                    itemsDiv.appendChild(artistDiv);
                }});

                nav.appendChild(catDiv);
            }});

            // Update stats
            const categories = Object.keys(structure).length;
            const artists = Object.values(structure).reduce((acc, cat) => acc + Object.keys(cat).length, 0);
            document.getElementById('stats').textContent = `${{data.length}} items / ${{artists}} artists / ${{categories}} categories`;
        }}

        function toggleCategory(header) {{
            header.parentElement.classList.toggle('open');
        }}

        function showArtist(category, artist, items) {{
            // Update active state
            document.querySelectorAll('.artist-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');

            // Update header
            document.getElementById('current-title').textContent = artist;
            document.getElementById('current-subtitle').textContent = `${{category}} / ${{items.length}} items`;

            // Store current items for actions
            currentItems = items;

            // Render items
            renderItems(items);

            // Close mobile menu after selection
            closeMobileMenu();
        }}

        function renderItems(items) {{
            const container = document.getElementById('items');
            container.innerHTML = items.map(item => `
                <div class="item" data-url="${{item.url}}">
                    <a href="${{item.url}}" target="_blank">
                        <img class="item-thumbnail"
                             src="https://archive.org/services/img/${{item.identifier}}"
                             alt="${{item.title}}"
                             loading="lazy"
                             onerror="this.style.display='none'">
                    </a>
                    <div class="item-title">
                        <a href="${{item.url}}" target="_blank">${{item.title}}</a>
                    </div>
                    <div class="item-meta">
                        <div class="item-meta-row">
                            <span>Confidence</span>
                            <span class="meta-value accent">${{item.confidence_score}}</span>
                        </div>
                        <div class="item-meta-row">
                            <span>Type</span>
                            <span class="meta-value">${{item.mediatype}}</span>
                        </div>
                    </div>
                    ${{item.creator ? `<div class="item-creator">${{item.creator}}</div>` : ''}}
                    <a href="${{item.url}}" target="_blank" class="open-link">View →</a>
                </div>
            `).join('');
        }}

        function openAllVisible() {{
            if (currentItems.length === 0) return;
            if (confirm(`Open ${{currentItems.length}} tabs?`)) {{
                currentItems.forEach(item => window.open(item.url, '_blank'));
            }}
        }}

        function copyVisibleUrls() {{
            if (currentItems.length === 0) return;
            const urls = currentItems.map(item => item.url).join('\\n');
            navigator.clipboard.writeText(urls).then(() => alert('URLs copied!'));
        }}

        // Load data
        fetch(CSV_FILE)
            .then(res => {{
                if (!res.ok) throw new Error('CSV not found');
                return res.text();
            }})
            .then(text => {{
                allData = parseCSV(text);
                buildNav(allData);
                document.getElementById('loading').style.display = 'none';
                document.getElementById('content').style.display = 'block';
            }})
            .catch(err => {{
                document.getElementById('loading').innerHTML = `
                    <p>Error loading data: ${{err.message}}</p>
                    <p style="margin-top: 1rem;">Make sure <code>${{CSV_FILE}}</code> exists in the same directory.</p>
                `;
            }});
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    console.print(f"[green]Generated HTML viewer at {output_path}[/green]")
    console.print(f"[dim]  → Reads data from: {csv_filename}[/dim]")
    console.print(f"[dim]  → Edit the CSV and refresh the browser to see changes[/dim]")
