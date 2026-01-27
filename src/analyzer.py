"""Analyze search results and compute confidence scores."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .searcher import search_archive, get_items_metadata_batch, build_search_query
from .filters import FilterConfig, ConfidenceResult, calculate_confidence, matches_search_intent, meets_engagement_threshold

console = Console()


@dataclass
class AnalyzedItem:
    """An item that has been analyzed and scored."""
    identifier: str
    title: str
    mediatype: str
    url: str
    confidence: ConfidenceResult
    search_term: str
    category: str = ""
    creator: Optional[str] = None
    publisher: Optional[str] = None
    page_count: Optional[int] = None

    @property
    def archive_url(self) -> str:
        return f"https://archive.org/details/{self.identifier}"


def analyze_category(
    category_name: str,
    category_config: dict,
    filter_config: FilterConfig,
    max_results_per_term: int = 50,
    fetch_full_metadata: bool = True,
) -> List[AnalyzedItem]:
    """Analyze all terms in a category and return scored items.

    Args:
        category_name: Name of the category being processed
        category_config: Category configuration with terms and mediatypes
        filter_config: Filtering and scoring configuration
        max_results_per_term: Max search results per term
        fetch_full_metadata: Whether to fetch full metadata for scoring

    Returns:
        List of AnalyzedItem objects that pass the confidence threshold
    """
    terms = category_config.get("terms", [])
    default_mediatypes = category_config.get("mediatype", ["texts"])

    console.print(f"\n[bold blue]Processing category: {category_name}[/bold blue]")
    console.print(f"  {len(terms)} terms, default mediatype: {default_mediatypes}")

    # Phase 1: Collect all search results (fast)
    seen_identifiers = set()
    candidates = []  # List of (item_dict, term_name, mediatype)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Searching...", total=len(terms))

        for term in terms:
            term_name = term if isinstance(term, str) else term.get("name", str(term))
            term_dict = term if isinstance(term, dict) else {"name": term}

            progress.update(task, description=f"Searching: {term_name}")

            # Get mediatypes for this term
            mediatypes = term_dict.get("mediatype", default_mediatypes)

            # Search for each mediatype
            for mediatype in mediatypes:
                query = build_search_query(term_dict, [mediatype])
                items = search_archive(query, max_results_per_term)

                for item in items:
                    identifier = item.get("identifier")

                    # Skip duplicates
                    if identifier in seen_identifiers:
                        continue
                    seen_identifiers.add(identifier)

                    # Quick relevance check
                    if not matches_search_intent(item, term_name):
                        continue

                    # Check engagement thresholds (views/favorites)
                    passes_engagement, _ = meets_engagement_threshold(item, filter_config)
                    if not passes_engagement:
                        continue

                    candidates.append((item, term_name, mediatype))

            progress.advance(task)

    console.print(f"  Found {len(candidates)} candidates after filtering")

    # Phase 2: Batch fetch metadata (parallel)
    metadata_map = {}
    if fetch_full_metadata and candidates:
        console.print(f"  Fetching metadata for {len(candidates)} items...")
        identifiers = [item["identifier"] for item, _, _ in candidates]
        metadata_map = get_items_metadata_batch(identifiers, include_files=False)

    # Phase 3: Score and create results
    results = []
    for item, term_name, mediatype in candidates:
        identifier = item["identifier"]
        metadata = metadata_map.get(identifier) if fetch_full_metadata else None

        # Calculate confidence
        confidence = calculate_confidence(
            item, metadata, mediatype, filter_config
        )

        # Create analyzed item
        analyzed = AnalyzedItem(
            identifier=identifier,
            title=item.get("title", "Unknown"),
            mediatype=item.get("mediatype", mediatype),
            url=f"https://archive.org/details/{identifier}",
            confidence=confidence,
            search_term=term_name,
            category=category_name,
            creator=_normalize_field(item.get("creator")),
            publisher=_normalize_field(item.get("publisher")),
            page_count=metadata.get("_page_count") if metadata else None,
        )

        results.append(analyzed)

    # Sort by confidence score descending
    results.sort(key=lambda x: x.confidence.score, reverse=True)

    # Report stats
    passing = [r for r in results if r.confidence.passes]
    console.print(f"  {len(passing)} items pass confidence threshold")

    return results


def _normalize_field(value) -> Optional[str]:
    """Normalize a metadata field that might be a string or list."""
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)
