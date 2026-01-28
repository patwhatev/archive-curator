"""Search archive.org for items matching search terms."""

from __future__ import annotations
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List
from internetarchive import search_items, get_item
from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)

# Maximum concurrent metadata fetches
MAX_WORKERS = 10


def fetch_user_favorites(username: str, max_results: int = 500) -> List[dict]:
    """Fetch items from a user's favorites on archive.org.

    Uses the search API with fav-USERNAME collection filter.

    Args:
        username: Archive.org username (without @)
        max_results: Maximum number of results to fetch

    Returns:
        List of dicts with item info
    """
    # Use the advanced search API to find favorited items
    api_url = "https://archive.org/advancedsearch.php"

    fields = [
        "identifier",
        "title",
        "mediatype",
        "creator",
        "publisher",
        "description",
        "downloads",
    ]

    params = {
        "q": f"collection:fav-{username}",
        "fl[]": fields,
        "output": "json",
        "rows": max_results,
    }

    try:
        response = requests.get(api_url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

        docs = data.get("response", {}).get("docs", [])
        total = data.get("response", {}).get("numFound", 0)

        console.print(f"[dim]Found {total} items in favorites (fetched {len(docs)})[/dim]")

        return docs

    except requests.RequestException as e:
        console.print(f"[red]Error fetching favorites: {e}[/red]")
        return []
    except (KeyError, ValueError) as e:
        console.print(f"[red]Error parsing response: {e}[/red]")
        return []


def fetch_list_items(list_url: str) -> List[dict]:
    """Fetch items from an archive.org user list/favorites.

    Args:
        list_url: URL to the archive.org list or username

    Returns:
        List of dicts with basic item info
    """
    import re

    # Extract username from URL
    # URL format: https://archive.org/details/@username/lists/N/list-name
    # Or just: @username or username
    match = re.search(r'@([^/]+)', list_url)
    if match:
        username = match.group(1)
    else:
        # Assume it's just a username
        username = list_url.lstrip('@')

    return fetch_user_favorites(username)


def fetch_item_basic_info(identifier: str) -> Optional[dict]:
    """Fetch basic info for a single item (lighter than full metadata).

    Args:
        identifier: Archive.org item identifier

    Returns:
        Dict with basic item info or None
    """
    try:
        item = get_item(identifier)
        if not item.exists:
            return None

        metadata = item.metadata
        return {
            "identifier": identifier,
            "title": metadata.get("title", "Unknown"),
            "mediatype": metadata.get("mediatype", "unknown"),
            "creator": metadata.get("creator"),
            "publisher": metadata.get("publisher"),
            "description": metadata.get("description", ""),
            "collection": metadata.get("collection", []),
            "downloads": metadata.get("downloads", 0),
        }
    except Exception as e:
        logger.debug(f"Error fetching {identifier}: {e}")
        return None


def fetch_items_basic_info_batch(identifiers: List[str]) -> dict[str, Optional[dict]]:
    """Fetch basic info for multiple items in parallel.

    Args:
        identifiers: List of archive.org item identifiers

    Returns:
        Dict mapping identifier to item info (or None if fetch failed)
    """
    results = {}

    if not identifiers:
        return results

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(fetch_item_basic_info, id): id
            for id in identifiers
        }

        for future in as_completed(future_to_id):
            identifier = future_to_id[future]
            try:
                results[identifier] = future.result()
            except Exception as e:
                logger.debug(f"Error fetching {identifier}: {e}")
                results[identifier] = None

    return results


def build_search_query(term: dict, mediatypes: List[str]) -> str:
    """Build an archive.org search query for a term.

    Args:
        term: Term dict with 'name' and optional 'search_term', 'mediatype'
        mediatypes: Default mediatypes from category

    Returns:
        Search query string
    """
    # Use custom search term if provided, otherwise use name
    search_text = term.get("search_term", term["name"])

    # Use term-specific mediatype if provided, otherwise use category default
    types = term.get("mediatype", mediatypes)

    # Build mediatype filter
    if len(types) == 1:
        mediatype_filter = f"mediatype:{types[0]}"
    else:
        mediatype_filter = "(" + " OR ".join(f"mediatype:{t}" for t in types) + ")"

    return f"({search_text}) AND {mediatype_filter}"


def search_archive(
    query: str,
    max_results: int = 50,
) -> List[dict]:
    """Search archive.org and return results with basic metadata.

    Args:
        query: Search query string
        max_results: Maximum number of results to return

    Returns:
        List of dicts with item identifier and basic metadata
    """
    fields = [
        "identifier",
        "title",
        "mediatype",
        "creator",
        "publisher",
        "date",
        "description",
        "collection",
        "downloads",
        "num_favorites",
    ]

    logger.debug(f"Searching: {query}")

    try:
        # Use params to limit results (rows parameter for archive.org API)
        # Sort by downloads descending to get the most popular/relevant items first
        results = search_items(
            query,
            fields=fields,
            params={"rows": max_results},
            sorts=["downloads desc"]
        )

        items = []
        for result in results:
            if len(items) >= max_results:
                break

            items.append({
                "identifier": result.get("identifier"),
                "title": result.get("title", "Unknown"),
                "mediatype": result.get("mediatype"),
                "creator": result.get("creator"),
                "publisher": result.get("publisher"),
                "date": result.get("date"),
                "description": result.get("description", ""),
                "collection": result.get("collection", []),
                "downloads": result.get("downloads", 0),
                "num_favorites": result.get("num_favorites", 0),
            })

        logger.debug(f"Found {len(items)} results for query")
        return items

    except Exception as e:
        console.print(f"[red]Search error for query '{query[:50]}...': {e}[/red]")
        logger.exception(f"Search failed: {query}")
        return []


def get_item_metadata(identifier: str, include_files: bool = False) -> Optional[dict]:
    """Fetch full metadata for a specific item.

    Args:
        identifier: Archive.org item identifier
        include_files: Whether to fetch file list (slower)

    Returns:
        Full metadata dict or None if not found
    """
    try:
        item = get_item(identifier)
        if not item.exists:
            return None

        metadata = dict(item.metadata)

        # Only fetch files if requested (this is slow)
        if include_files:
            files = list(item.get_files())
            metadata["_files"] = [
                {
                    "name": f.name,
                    "format": f.format,
                    "size": f.size,
                }
                for f in files
            ]
            metadata["_page_count"] = _extract_page_count(metadata, files)
        else:
            # Try to get page count from metadata only
            metadata["_page_count"] = _extract_page_count_from_metadata(metadata)

        return metadata

    except Exception as e:
        logger.debug(f"Error fetching {identifier}: {e}")
        return None


def get_items_metadata_batch(identifiers: List[str], include_files: bool = False) -> dict[str, Optional[dict]]:
    """Fetch metadata for multiple items in parallel.

    Args:
        identifiers: List of archive.org item identifiers
        include_files: Whether to fetch file lists (slower)

    Returns:
        Dict mapping identifier to metadata (or None if fetch failed)
    """
    results = {}

    if not identifiers:
        return results

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(get_item_metadata, id, include_files): id
            for id in identifiers
        }

        for future in as_completed(future_to_id):
            identifier = future_to_id[future]
            try:
                results[identifier] = future.result()
            except Exception as e:
                logger.debug(f"Error fetching {identifier}: {e}")
                results[identifier] = None

    return results


def _extract_page_count_from_metadata(metadata: dict) -> Optional[int]:
    """Extract page count from metadata fields only (no file listing)."""
    for field in ["imagecount", "pages", "page_count", "num_pages"]:
        if field in metadata:
            try:
                return int(metadata[field])
            except (ValueError, TypeError):
                pass
    return None


def _extract_page_count(metadata: dict, files: list) -> Optional[int]:
    """Extract page count from metadata or file information.

    Args:
        metadata: Item metadata dict
        files: List of item files

    Returns:
        Page count or None if not determinable
    """
    # Check common metadata fields for page count
    result = _extract_page_count_from_metadata(metadata)
    if result is not None:
        return result

    # Count image files (often represents pages in scanned books)
    image_extensions = {".jp2", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    image_count = sum(
        1 for f in files
        if any(f.name.lower().endswith(ext) for ext in image_extensions)
    )
    if image_count > 0:
        return image_count

    return None
