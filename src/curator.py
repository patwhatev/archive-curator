"""Add items to archive.org lists."""
from __future__ import annotations
import os
import json
import time
import requests
from dataclasses import dataclass
from typing import Optional, Tuple, Set
from rich.console import Console

console = Console()


@dataclass
class ListConfig:
    """Configuration for the target archive.org list."""
    parent: str  # e.g., "@80081355"
    list_name: str  # e.g., "culture-library"
    access_key: str
    secret_key: str
    description: str = ""

    @classmethod
    def from_env(cls) -> "ListConfig":
        """Create ListConfig from environment variables."""
        access_key = os.getenv("IA_ACCESS_KEY_ID")
        secret_key = os.getenv("IA_SECRET_ACCESS_KEY")
        parent = os.getenv("IA_LIST_PARENT")
        list_name = os.getenv("IA_LIST_NAME")

        if not all([access_key, secret_key, parent, list_name]):
            missing = []
            if not access_key:
                missing.append("IA_ACCESS_KEY_ID")
            if not secret_key:
                missing.append("IA_SECRET_ACCESS_KEY")
            if not parent:
                missing.append("IA_LIST_PARENT")
            if not list_name:
                missing.append("IA_LIST_NAME")
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")

        return cls(
            parent=parent,
            list_name=list_name,
            access_key=access_key,
            secret_key=secret_key,
        )

    @classmethod
    def from_dict(cls, list_dict: dict) -> "ListConfig":
        """Create ListConfig from a list definition dict (from lists.yaml)."""
        access_key = os.getenv("IA_ACCESS_KEY_ID")
        secret_key = os.getenv("IA_SECRET_ACCESS_KEY")

        if not access_key or not secret_key:
            missing = []
            if not access_key:
                missing.append("IA_ACCESS_KEY_ID")
            if not secret_key:
                missing.append("IA_SECRET_ACCESS_KEY")
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")

        return cls(
            parent=list_dict["parent"],
            list_name=list_dict["name"],
            access_key=access_key,
            secret_key=secret_key,
            description=list_dict.get("description", ""),
        )

    @property
    def url(self) -> str:
        """Get the archive.org URL for this list."""
        return f"https://archive.org/details/{self.parent}/lists/{self.list_name}"


def add_to_list(
    identifier: str,
    list_config: ListConfig,
    notes: Optional[dict] = None,
) -> bool:
    """Add an item to an archive.org list.

    Args:
        identifier: The archive.org item identifier
        list_config: List configuration with credentials
        notes: Optional notes to attach to the list entry

    Returns:
        True if successful, False otherwise
    """
    url = f"https://archive.org/metadata/{identifier}"

    patch = {
        "op": "set",
        "parent": list_config.parent,
        "list": list_config.list_name,
        "notes": notes or {},
    }

    data = {
        "-target": "simplelists",
        "-patch": json.dumps(patch),
    }

    headers = {
        "Authorization": f"LOW {list_config.access_key}:{list_config.secret_key}",
    }

    try:
        response = requests.post(url, data=data, headers=headers)

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                return True
            else:
                console.print(f"[yellow]API returned success=false for {identifier}: {result}[/yellow]")
                return False
        else:
            console.print(f"[red]HTTP {response.status_code} adding {identifier}: {response.text}[/red]")
            return False

    except Exception as e:
        console.print(f"[red]Error adding {identifier} to list: {e}[/red]")
        return False


def add_items_to_list(
    items: list,
    list_config: ListConfig,
    rate_limit: float = 1.0,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Add multiple items to an archive.org list.

    Args:
        items: List of AnalyzedItem objects to add
        list_config: List configuration with credentials
        rate_limit: Seconds between API calls
        dry_run: If True, don't actually add items

    Returns:
        Tuple of (successful_count, failed_count)
    """
    success_count = 0
    fail_count = 0

    for item in items:
        if dry_run:
            console.print(f"  [dim]DRY RUN: Would add {item.identifier}[/dim]")
            success_count += 1
            continue

        # Include metadata in notes for reference
        notes = {
            "search_term": item.search_term,
            "confidence_score": item.confidence.score,
            "added_by": "archive-curator",
        }

        if add_to_list(item.identifier, list_config, notes):
            console.print(f"  [green]\u2713[/green] Added: {item.identifier}")
            success_count += 1
        else:
            console.print(f"  [red]\u2717[/red] Failed: {item.identifier}")
            fail_count += 1

        time.sleep(rate_limit)

    return success_count, fail_count


def get_existing_list_items(list_config: ListConfig) -> Set[str]:
    """Get identifiers of items already in the list.

    Args:
        list_config: List configuration

    Returns:
        Set of item identifiers already in the list
    """
    from internetarchive import search_items

    # Query for items in this list
    query = f"simplelists__{list_config.list_name}:{list_config.parent}"

    try:
        results = search_items(query, fields=["identifier"])
        return {r["identifier"] for r in results}
    except Exception as e:
        console.print(f"[yellow]Warning: Could not fetch existing list items: {e}[/yellow]")
        return set()
