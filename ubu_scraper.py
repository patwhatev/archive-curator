#!/usr/bin/env python3
"""
UbuWeb Scraper - Scrapes ubu.com categories, artists, and works using Playwright.
Outputs CSV files per category to ubu_data/ directory.
"""

import asyncio
import csv
from pathlib import Path
from urllib.parse import urljoin

import click
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

BASE_URL = "https://ubu.com"
OUTPUT_DIR = Path("ubu_data")
EXCLUDED_CATEGORIES = ["About", "Contact"]
EXCLUDED_LINKS = ["siteIndex.html", "index", "site_index"]  # Common nav links to skip

MAX_RETRIES = 3
NAV_TIMEOUT = 30000  # 30 seconds


async def get_category_links(page):
    """Get all category links from the homepage."""
    await page.goto(BASE_URL, timeout=NAV_TIMEOUT)
    await page.wait_for_load_state("domcontentloaded")

    links = await page.query_selector_all(".nav-column a")
    categories = []

    for link in links:
        name = await link.inner_text()
        href = await link.get_attribute("href")

        if name.strip() not in EXCLUDED_CATEGORIES and href:
            full_url = urljoin(BASE_URL + "/", href)
            categories.append({"name": name.strip(), "url": full_url})

    return categories


async def get_artist_links(page, category_url):
    """Get all artist links from a category page."""
    for attempt in range(MAX_RETRIES):
        try:
            await page.goto(category_url, timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            break
        except PlaywrightTimeout:
            if attempt == MAX_RETRIES - 1:
                console.print(f"[red]Failed to load category: {category_url}[/red]")
                return []
            await asyncio.sleep(2)

    # Get links from the main content area (not navigation)
    # Most category pages have links directly in the body
    links = await page.query_selector_all("body a")
    artists = []
    seen_urls = set()

    for link in links:
        name = await link.inner_text()
        href = await link.get_attribute("href")

        if not href or not name.strip():
            continue

        # Skip navigation links and external links
        if href.startswith("http") and "ubu.com" not in href:
            continue
        if href in ["#", "/"]:
            continue
        # Skip navigation index links (but allow subdirectory index.html like "cage/index.html")
        if href == "index.html" or href == "../index.html" or href.startswith("../"):
            continue
        # Skip common navigation links
        if any(excl in href for excl in EXCLUDED_LINKS):
            continue
        if any(exc.lower() in name.lower() for exc in EXCLUDED_CATEGORIES):
            continue

        full_url = urljoin(category_url, href)

        # Skip if we've seen this URL or it's the same as category
        if full_url in seen_urls or full_url == category_url:
            continue

        # Only include links that look like artist pages (end in .html or are subdirs)
        if ".html" in href or href.endswith("/"):
            seen_urls.add(full_url)
            artists.append({"name": name.strip(), "url": full_url})

    return artists


async def get_artist_works(page, artist_url):
    """Get all work links from an artist page."""
    for attempt in range(MAX_RETRIES):
        try:
            await page.goto(artist_url, timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            break
        except PlaywrightTimeout:
            if attempt == MAX_RETRIES - 1:
                return [], []
            await asyncio.sleep(2)

    links = await page.query_selector_all("body a")
    work_titles = []
    work_urls = []
    seen = set()

    for link in links:
        title = await link.inner_text()
        href = await link.get_attribute("href")

        if not href or not title.strip():
            continue

        # Skip navigation and self-references
        if href in ["#", "/"]:
            continue
        # Skip navigation index links (but allow subdirectory index.html)
        if href == "index.html" or href == "../index.html" or href.startswith("../"):
            continue
        # Skip common navigation links
        if any(excl in href for excl in EXCLUDED_LINKS):
            continue
        if href.startswith("http") and "ubu.com" not in href:
            continue

        full_url = urljoin(artist_url, href)

        if full_url in seen or full_url == artist_url:
            continue

        # Include media files and html pages
        if any(ext in href.lower() for ext in [".html", ".mp3", ".mp4", ".pdf", ".mov", ".wav"]):
            seen.add(full_url)
            work_titles.append(title.strip())
            work_urls.append(full_url)

    return work_titles, work_urls


async def scrape_aspen_magazine(page, category, debug=False):
    """Special handler for Aspen Magazine - grabs tbody content for each issue."""
    if debug:
        console.print(f"\n[yellow]DEBUG: Scraping Aspen Magazine specially[/yellow]")

    # Hardcoded issue info
    issues = [
        {"name": "No. 1: The Black Box", "url": "https://ubu.com/aspen/aspen1/index.html"},
        {"name": "No. 2: The White Box", "url": "https://ubu.com/aspen/aspen2/index.html"},
        {"name": "No. 3: The Pop Art Issue", "url": "https://ubu.com/aspen/aspen3/index.html"},
        {"name": "No. 4: The McLuhan Issue", "url": "https://ubu.com/aspen/aspen4/index.html"},
        {"name": "No. 5+6: The Minimalism Issue", "url": "https://ubu.com/aspen/aspen5and6/index.html"},
        {"name": "No. 6A: The Performance Issue", "url": "https://ubu.com/aspen/aspen6A/index.html"},
        {"name": "No. 7: The British Issue", "url": "https://ubu.com/aspen/aspen7/index.html"},
        {"name": "No. 8: The Fluxus Issue", "url": "https://ubu.com/aspen/aspen8/index.html"},
        {"name": "No. 9: The Psychedelic Issue", "url": "https://ubu.com/aspen/aspen9/index.html"},
        {"name": "No. 10: The Asia Issue", "url": "https://ubu.com/aspen/aspen10/index.html"},
    ]

    results = []

    for issue in issues:
        if debug:
            console.print(f"[yellow]DEBUG: Fetching {issue['name']}[/yellow]")

        try:
            await page.goto(issue["url"], timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")

            # Get body content, then remove navigation elements via JS
            content_html = await page.evaluate('''() => {
                // Clone the body to avoid modifying the actual page
                const clone = document.body.cloneNode(true);

                // Remove navigation tables (usually at top and bottom)
                const tables = clone.querySelectorAll('table');
                tables.forEach(t => t.remove());

                // Remove script tags
                const scripts = clone.querySelectorAll('script');
                scripts.forEach(s => s.remove());

                return clone.innerHTML;
            }''')

            # Escape newlines so CSV parsing doesn't break
            clean_html = content_html.strip().replace('\n', '&#10;').replace('\r', '') if content_html else ""

            results.append({
                "artist_name": issue["name"],
                "artist_url": issue["url"],
                "works": "",
                "work_urls": "",
                "content_html": clean_html,
                "category": category["name"],
            })

            if debug:
                console.print(f"[yellow]DEBUG: Captured {len(content_html)} chars of content[/yellow]")

        except Exception as e:
            if debug:
                console.print(f"[red]DEBUG: Failed to fetch {issue['name']}: {e}[/red]")

    return results


async def scrape_category(page, category, limit=None, progress=None, task_id=None, debug=False):
    """Scrape a single category and return artist data."""
    # Special handling for Aspen Magazine
    if "aspen" in category["name"].lower():
        return await scrape_aspen_magazine(page, category, debug=debug)

    if debug:
        console.print(f"\n[yellow]DEBUG: Fetching artists from {category['url']}[/yellow]")

    artists = await get_artist_links(page, category["url"])

    if debug:
        console.print(f"[yellow]DEBUG: Found {len(artists)} artists[/yellow]")
        for i, a in enumerate(artists[:5]):
            console.print(f"[yellow]  {i+1}. {a['name']} -> {a['url']}[/yellow]")
        if len(artists) > 5:
            console.print(f"[yellow]  ... and {len(artists) - 5} more[/yellow]")

    if limit:
        artists = artists[:limit]

    results = []
    total = len(artists)

    for i, artist in enumerate(artists):
        if progress and task_id:
            progress.update(task_id, description=f"[cyan]{category['name']}[/cyan]: {artist['name']} ({i+1}/{total})")

        if debug:
            console.print(f"\n[yellow]DEBUG: Scraping artist: {artist['name']} at {artist['url']}[/yellow]")

        work_titles, work_urls = await get_artist_works(page, artist["url"])

        if debug:
            console.print(f"[yellow]DEBUG: Found {len(work_titles)} works[/yellow]")
            for j, title in enumerate(work_titles[:3]):
                console.print(f"[yellow]  {j+1}. {title}[/yellow]")

        results.append({
            "artist_name": artist["name"],
            "artist_url": artist["url"],
            "works": ", ".join(work_titles) if work_titles else "",
            "work_urls": ", ".join(work_urls) if work_urls else "",
            "category": category["name"],
        })

        if debug:
            console.print(f"[yellow]DEBUG: Stored row -> artist_name: {artist['name']}[/yellow]")

    return results


def write_csv(category_name, data):
    """Write category data to CSV file."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Sanitize filename
    filename = category_name.lower().replace(" ", "_").replace("/", "_").replace("&", "and")
    filepath = OUTPUT_DIR / f"{filename}.csv"

    # Check if any row has content_html (for Aspen Magazine)
    has_html_content = any(row.get("content_html") for row in data)

    if has_html_content:
        fieldnames = ["artist_name", "artist_url", "works", "work_urls", "content_html", "category"]
    else:
        fieldnames = ["artist_name", "artist_url", "works", "work_urls", "category"]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(data)

    return filepath


async def run_scraper(visible=False, category=None, limit=None, test=False, debug=False):
    """Main scraper function."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not visible)
        page = await browser.new_page()

        console.print("[bold]UbuWeb Scraper[/bold]")
        console.print(f"Output directory: {OUTPUT_DIR.absolute()}\n")

        # Get categories
        console.print("Fetching categories from homepage...")
        categories = await get_category_links(page)
        console.print(f"Found {len(categories)} categories\n")

        if debug:
            console.print("[yellow]DEBUG: Categories found:[/yellow]")
            for c in categories:
                console.print(f"[yellow]  - {c['name']} -> {c['url']}[/yellow]")

        # Filter to specific category if requested
        if category:
            categories = [c for c in categories if c["name"].lower() == category.lower()]
            if not categories:
                console.print(f"[red]Category '{category}' not found[/red]")
                await browser.close()
                return

        # Test mode: just first category, first artist
        if test:
            categories = categories[:1]
            limit = 5
            debug = True  # Always enable debug in test mode
            console.print("[yellow]Test mode: scraping 1 category, 1 artist (debug enabled)[/yellow]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            for cat in categories:
                task_id = progress.add_task(f"[cyan]{cat['name']}[/cyan]: starting...", total=None)

                results = await scrape_category(page, cat, limit=limit, progress=progress, task_id=task_id, debug=debug)

                if results:
                    filepath = write_csv(cat["name"], results)
                    progress.update(task_id, description=f"[green]{cat['name']}[/green]: {len(results)} artists -> {filepath}")
                else:
                    progress.update(task_id, description=f"[yellow]{cat['name']}[/yellow]: no artists found")

                progress.remove_task(task_id)
                console.print(f"[green]Completed:[/green] {cat['name']} - {len(results)} artists")

        await browser.close()
        console.print(f"\n[bold green]Done![/bold green] Files saved to {OUTPUT_DIR.absolute()}")


@click.command()
@click.option("--visible", is_flag=True, help="Show browser window (non-headless mode)")
@click.option("--category", "-c", help="Scrape only a specific category")
@click.option("--limit", "-l", type=int, help="Limit number of artists per category")
@click.option("--test", "-t", is_flag=True, help="Test mode: scrape 1 category, 1 artist")
@click.option("--debug", "-d", is_flag=True, help="Enable debug logging")
def main(visible, category, limit, test, debug):
    """Scrape UbuWeb categories, artists, and works to CSV files."""
    asyncio.run(run_scraper(visible=visible, category=category, limit=limit, test=test, debug=debug))


if __name__ == "__main__":
    main()
