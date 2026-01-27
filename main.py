#!/usr/bin/env python3
"""Archive Curator - Search, filter, and save artifacts from archive.org."""

# Suppress urllib3 SSL warning (LibreSSL compatibility)
import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import os
import sys
import logging
from pathlib import Path
from datetime import datetime

import click
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt

from src.analyzer import analyze_category, AnalyzedItem
from src.filters import FilterConfig
from src.curator import ListConfig, add_items_to_list, get_existing_list_items
from src.exporter import export_to_csv, export_to_html, export_to_json, generate_html_viewer

console = Console()


def load_config(config_dir: Path, categories_file: str = None) -> tuple[dict, FilterConfig]:
    """Load category and filter configurations."""
    if categories_file:
        categories_path = Path(categories_file)
        if not categories_path.is_absolute():
            categories_path = config_dir / categories_file
    else:
        categories_path = config_dir / "categories.yaml"

    filters_path = config_dir / "filters.yaml"

    if not categories_path.exists():
        console.print(f"[red]Categories config not found: {categories_path}[/red]")
        sys.exit(1)

    if not filters_path.exists():
        console.print(f"[red]Filters config not found: {filters_path}[/red]")
        sys.exit(1)

    with open(categories_path) as f:
        categories = yaml.safe_load(f)

    with open(filters_path) as f:
        filters_raw = yaml.safe_load(f)

    filter_config = FilterConfig.from_yaml(filters_raw)

    return categories, filter_config


def load_lists(config_dir: Path) -> list[dict]:
    """Load available lists from config."""
    lists_path = config_dir / "lists.yaml"

    if not lists_path.exists():
        console.print(f"[yellow]Lists config not found: {lists_path}[/yellow]")
        return []

    with open(lists_path) as f:
        config = yaml.safe_load(f)

    return config.get("lists", [])


def select_list(lists: list[dict]) -> dict:
    """Interactive prompt to select a target list."""
    if not lists:
        console.print("[red]No lists configured. Add lists to config/lists.yaml[/red]")
        sys.exit(1)

    console.print("\n[bold]Select target list:[/bold]\n")

    for i, lst in enumerate(lists, 1):
        name = lst.get("name", "unnamed")
        desc = lst.get("description", "")
        console.print(f"  [cyan]{i}[/cyan]) {name}")
        if desc:
            console.print(f"      [dim]{desc}[/dim]")

    console.print()

    while True:
        choice = Prompt.ask(
            "Enter number",
            default="1"
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(lists):
                selected = lists[idx]
                console.print(f"\n[green]Selected:[/green] {selected['name']}\n")
                return selected
            else:
                console.print(f"[red]Please enter a number between 1 and {len(lists)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number[/red]")


def display_results(items: list[AnalyzedItem], show_all: bool = False):
    """Display analyzed items in a table."""
    if not items:
        console.print("[yellow]No items found.[/yellow]")
        return

    table = Table(title="Search Results", show_lines=True)
    table.add_column("Score", style="cyan", width=6)
    table.add_column("Status", width=6)
    table.add_column("Title", style="white", max_width=40)
    table.add_column("Type", width=8)
    table.add_column("Search Term", style="dim", max_width=20)
    table.add_column("Identifier", style="dim", max_width=30)

    for item in items:
        if not show_all and not item.confidence.passes:
            continue

        status = "[green]PASS[/green]" if item.confidence.passes else "[red]FAIL[/red]"
        table.add_row(
            str(item.confidence.score),
            status,
            item.title[:40] + "..." if len(item.title) > 40 else item.title,
            item.mediatype,
            item.search_term,
            item.identifier,
        )

    console.print(table)


def display_item_details(item: AnalyzedItem):
    """Display detailed information about a single item."""
    panel_content = f"""[bold]Title:[/bold] {item.title}
[bold]Identifier:[/bold] {item.identifier}
[bold]URL:[/bold] {item.url}
[bold]Type:[/bold] {item.mediatype}
[bold]Creator:[/bold] {item.creator or 'Unknown'}
[bold]Publisher:[/bold] {item.publisher or 'Unknown'}
[bold]Pages:[/bold] {item.page_count or 'N/A'}
[bold]Search Term:[/bold] {item.search_term}

[bold]Confidence Score:[/bold] {item.confidence.score}
[bold]Status:[/bold] {'PASS' if item.confidence.passes else 'FAIL'}

[bold]Scoring Reasons:[/bold]"""

    for reason in item.confidence.reasons:
        panel_content += f"\n  {reason}"

    if not item.confidence.reasons:
        panel_content += "\n  No adjustments (base score)"

    console.print(Panel(panel_content, title=f"Item Details", border_style="blue"))


@click.group()
@click.option("--config-dir", "-c", default="config", help="Config directory path")
@click.option("--debug", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx, config_dir, debug):
    """Archive Curator - Search, filter, and save artifacts from archive.org."""
    # Configure logging
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=debug)]
    )

    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = Path(config_dir)


@cli.command()
@click.option("--category", "-t", help="Specific category to search (default: all)")
@click.option("--max-results", "-m", default=150, help="Max results per search term")
@click.option("--show-all", "-a", is_flag=True, help="Show all results, not just passing")
@click.option("--details", "-d", is_flag=True, help="Show detailed scoring for each item")
@click.option("--no-metadata", is_flag=True, help="Skip fetching full metadata (faster but less accurate)")
@click.option("--categories-file", "-f", help="Use alternate categories file (e.g., test-categories.yaml)")
@click.option("--export", "-e", type=click.Choice(["csv", "html", "json"]), help="Export results to file")
@click.option("--output", "-o", help="Output file path (default: output/results.[format])")
@click.option("--append", is_flag=True, help="Append to existing CSV instead of overwriting (only for csv export)")
@click.pass_context
def search(ctx, category, max_results, show_all, details, no_metadata, categories_file, export, output, append):
    """Search archive.org and display results with confidence scores."""
    config_dir = ctx.obj["config_dir"]
    categories, filter_config = load_config(config_dir, categories_file)

    # Filter to specific category if requested
    if category:
        if category not in categories:
            console.print(f"[red]Unknown category: {category}[/red]")
            console.print(f"Available: {', '.join(categories.keys())}")
            sys.exit(1)
        categories = {category: categories[category]}

    all_results = []

    for cat_name, cat_config in categories.items():
        results = analyze_category(
            cat_name,
            cat_config,
            filter_config,
            max_results_per_term=max_results,
            fetch_full_metadata=not no_metadata,
        )
        all_results.extend(results)

    # Display results
    display_results(all_results, show_all=show_all)

    if details:
        passing = [r for r in all_results if r.confidence.passes]
        console.print(f"\n[bold]Detailed scoring for {len(passing)} passing items:[/bold]\n")
        for item in passing[:20]:  # Limit to first 20
            display_item_details(item)
            console.print()

    # Summary
    passing_count = sum(1 for r in all_results if r.confidence.passes)
    console.print(f"\n[bold]Summary:[/bold] {passing_count} of {len(all_results)} items pass confidence threshold")

    # Export if requested
    if export:
        # Determine output path
        if output:
            output_path = Path(output)
        else:
            output_dir = Path("output")
            output_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = output_dir / f"results_{timestamp}.{export}"

        if export == "csv":
            export_to_csv(all_results, output_path, include_failed=show_all, append=append)
        elif export == "html":
            export_to_html(all_results, output_path, include_failed=show_all)
        elif export == "json":
            export_to_json(all_results, output_path, include_failed=show_all)


@cli.command()
@click.option("--category", "-t", help="Specific category to process (default: all)")
@click.option("--max-results", "-m", default=50, help="Max results per search term")
@click.option("--dry-run", "-n", is_flag=True, help="Preview without adding to list")
@click.option("--skip-existing", "-s", is_flag=True, help="Skip items already in list")
@click.option("--min-confidence", type=int, help="Override minimum confidence score")
@click.option("--categories-file", "-f", help="Use alternate categories file (e.g., test-categories.yaml)")
@click.option("--list", "-l", "list_name", help="Target list name (skip interactive selection)")
@click.pass_context
def curate(ctx, category, max_results, dry_run, skip_existing, min_confidence, categories_file, list_name):
    """Search, filter, and add passing items to your archive.org list."""
    config_dir = ctx.obj["config_dir"]
    categories, filter_config = load_config(config_dir, categories_file)

    # Override min confidence if specified
    if min_confidence is not None:
        filter_config.min_confidence = min_confidence

    # Load available lists
    lists = load_lists(config_dir)

    # Select target list (interactive or by name)
    if list_name:
        # Find list by name
        selected_list = None
        for lst in lists:
            if lst.get("name") == list_name:
                selected_list = lst
                break
        if not selected_list:
            console.print(f"[red]List not found: {list_name}[/red]")
            console.print(f"Available lists: {', '.join(l['name'] for l in lists)}")
            sys.exit(1)
        console.print(f"[green]Using list:[/green] {selected_list['name']}")
    else:
        # Interactive selection
        selected_list = select_list(lists)

    # Create list config from selection
    try:
        list_config = ListConfig.from_dict(selected_list)
    except ValueError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        console.print("Ensure IA_ACCESS_KEY_ID and IA_SECRET_ACCESS_KEY are set in your environment.")
        sys.exit(1)

    if dry_run:
        console.print(Panel("[yellow]DRY RUN MODE - No changes will be made[/yellow]"))

    # Get existing items if skip_existing is enabled
    existing_items = set()
    if skip_existing:
        console.print("Fetching existing list items...")
        existing_items = get_existing_list_items(list_config)
        console.print(f"Found {len(existing_items)} items already in list")

    # Filter to specific category if requested
    if category:
        if category not in categories:
            console.print(f"[red]Unknown category: {category}[/red]")
            console.print(f"Available: {', '.join(categories.keys())}")
            sys.exit(1)
        categories = {category: categories[category]}

    total_added = 0
    total_failed = 0
    total_skipped = 0

    for cat_name, cat_config in categories.items():
        results = analyze_category(
            cat_name,
            cat_config,
            filter_config,
            max_results_per_term=max_results,
        )

        # Filter to passing items
        passing = [r for r in results if r.confidence.passes]

        # Skip existing items
        if skip_existing:
            before_count = len(passing)
            passing = [r for r in passing if r.identifier not in existing_items]
            skipped = before_count - len(passing)
            total_skipped += skipped
            if skipped:
                console.print(f"  Skipping {skipped} items already in list")

        if not passing:
            console.print(f"  No new items to add for {cat_name}")
            continue

        # Display what will be added
        console.print(f"\n[bold]Adding {len(passing)} items from {cat_name}:[/bold]")
        display_results(passing)

        # Add to list
        added, failed = add_items_to_list(
            passing,
            list_config,
            dry_run=dry_run,
        )
        total_added += added
        total_failed += failed

    # Final summary
    console.print(f"\n[bold]Final Summary:[/bold]")
    console.print(f"  Added: {total_added}")
    console.print(f"  Failed: {total_failed}")
    console.print(f"  Skipped (existing): {total_skipped}")

    if dry_run:
        console.print("\n[yellow]This was a dry run. Run without --dry-run to actually add items.[/yellow]")


@cli.command()
@click.pass_context
def categories(ctx):
    """List available categories and their terms."""
    config_dir = ctx.obj["config_dir"]
    categories_config, _ = load_config(config_dir)

    for cat_name, cat_config in categories_config.items():
        terms = cat_config.get("terms", [])
        mediatypes = cat_config.get("mediatype", ["texts"])
        description = cat_config.get("description", "")

        console.print(f"\n[bold blue]{cat_name}[/bold blue]")
        if description:
            console.print(f"  {description}")
        console.print(f"  Default mediatype: {mediatypes}")
        console.print(f"  Terms ({len(terms)}):")

        for term in terms:
            if isinstance(term, str):
                console.print(f"    - {term}")
            else:
                name = term.get("name", "?")
                custom_search = term.get("search_term")
                custom_types = term.get("mediatype")
                extras = []
                if custom_search:
                    extras.append(f'search: "{custom_search}"')
                if custom_types:
                    extras.append(f"types: {custom_types}")
                extra_str = f" ({', '.join(extras)})" if extras else ""
                console.print(f"    - {name}{extra_str}")


@cli.command()
@click.pass_context
def check_auth(ctx):
    """Verify archive.org credentials are configured correctly."""
    try:
        list_config = ListConfig.from_env()
        console.print("[green]Credentials loaded successfully![/green]")
        console.print(f"  List parent: {list_config.parent}")
        console.print(f"  List name: {list_config.list_name}")
        console.print(f"  Access key: {list_config.access_key[:8]}...")

        # Try to fetch existing items as a connection test
        console.print("\nTesting API connection...")
        existing = get_existing_list_items(list_config)
        console.print(f"[green]Success![/green] Found {len(existing)} items in your list.")

    except ValueError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        console.print("\nPlease ensure you have a .env file with:")
        console.print("  IA_ACCESS_KEY_ID=your_access_key")
        console.print("  IA_SECRET_ACCESS_KEY=your_secret_key")
        console.print("  IA_LIST_PARENT=@your_username")
        console.print("  IA_LIST_NAME=your_list_name")
        console.print("\nGet your S3 keys from: https://archive.org/account/s3.php")
        sys.exit(1)


@cli.command()
@click.option("--csv", "-c", default="data.csv", help="CSV data file name (default: data.csv)")
@click.option("--output", "-o", default="output/viewer.html", help="Output HTML file path")
@click.option("--title", "-t", default="Archive Curator", help="Page title")
def viewer(csv, output, title):
    """Generate an HTML viewer that reads from a CSV file.

    The viewer loads data from the CSV dynamically, so you can edit
    the CSV and refresh the browser to see changes immediately.

    Example workflow:
        1. python main.py search -e csv -o output/data.csv
        2. python main.py viewer --csv data.csv
        3. Open output/viewer.html in browser
        4. Edit output/data.csv as needed, refresh browser
    """
    output_path = Path(output)
    output_path.parent.mkdir(exist_ok=True)

    generate_html_viewer(csv, output_path, title)

    console.print(f"\n[bold]Workflow:[/bold]")
    console.print(f"  1. Export search results: [cyan]python main.py search -e csv -o output/{csv}[/cyan]")
    console.print(f"  2. Open [cyan]{output}[/cyan] in your browser")
    console.print(f"  3. Edit [cyan]output/{csv}[/cyan] to add/modify items")
    console.print(f"  4. Refresh browser to see changes")


@cli.command()
@click.option("--source-csv", "-c", default="output/data.csv", help="Source CSV file path")
@click.option("--source-html", "-h", default="output/viewer.html", help="Source HTML viewer path")
@click.option("--deploy-dir", "-d", default="docs", help="Deployment directory (GitHub Pages uses /docs)")
@click.option("--commit", is_flag=True, help="Also git commit the changes")
@click.option("--push", is_flag=True, help="Also git push after commit (implies --commit)")
def deploy(source_csv, source_html, deploy_dir, commit, push):
    """Deploy viewer and data to a directory for GitHub Pages.

    Copies the HTML viewer (as index.html) and CSV data to a deployment
    directory that can be served by GitHub Pages.

    Example:
        python main.py deploy
        python main.py deploy --commit --push
    """
    import shutil

    deploy_path = Path(deploy_dir)
    source_csv_path = Path(source_csv)
    source_html_path = Path(source_html)

    # Validate source files exist
    if not source_csv_path.exists():
        console.print(f"[red]CSV file not found: {source_csv}[/red]")
        console.print(f"Run: [cyan]python main.py search -e csv -o {source_csv}[/cyan]")
        sys.exit(1)

    if not source_html_path.exists():
        console.print(f"[red]HTML viewer not found: {source_html}[/red]")
        console.print(f"Run: [cyan]python main.py viewer[/cyan]")
        sys.exit(1)

    # Create deploy directory
    deploy_path.mkdir(exist_ok=True)

    # Copy files
    dest_html = deploy_path / "index.html"
    dest_csv = deploy_path / "data.csv"

    shutil.copy2(source_html_path, dest_html)
    shutil.copy2(source_csv_path, dest_csv)

    console.print(f"[green]Deployed to {deploy_dir}/[/green]")
    console.print(f"  {dest_html}")
    console.print(f"  {dest_csv}")

    # Git operations
    if push:
        commit = True  # --push implies --commit

    if commit:
        import subprocess

        try:
            # Add deployed files
            subprocess.run(["git", "add", str(deploy_path)], check=True)

            # Commit
            result = subprocess.run(
                ["git", "commit", "-m", "Deploy: update viewer and data"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                console.print(f"[green]Committed changes[/green]")
            elif "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                console.print(f"[yellow]No changes to commit[/yellow]")
            else:
                console.print(f"[red]Commit failed: {result.stderr}[/red]")
                sys.exit(1)

            if push:
                subprocess.run(["git", "push"], check=True)
                console.print(f"[green]Pushed to remote[/green]")

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Git error: {e}[/red]")
            sys.exit(1)
        except FileNotFoundError:
            console.print(f"[red]Git not found. Install git or run commands manually.[/red]")
            sys.exit(1)
    else:
        console.print(f"\n[bold]To publish:[/bold]")
        console.print(f"  [cyan]git add {deploy_dir}[/cyan]")
        console.print(f"  [cyan]git commit -m 'Deploy: update viewer and data'[/cyan]")
        console.print(f"  [cyan]git push[/cyan]")
        console.print(f"\nOr run: [cyan]python main.py deploy --commit --push[/cyan]")

    console.print(f"\n[bold]GitHub Pages setup:[/bold]")
    console.print(f"  1. Go to repo Settings → Pages")
    console.print(f"  2. Set source to: [cyan]Deploy from a branch[/cyan]")
    console.print(f"  3. Set branch to: [cyan]main[/cyan] and folder to: [cyan]/{deploy_dir}[/cyan]")


if __name__ == "__main__":
    cli()
