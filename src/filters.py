"""Confidence scoring and filtering logic."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class FilterConfig:
    """Configuration for filtering and scoring."""
    min_confidence: int = 60
    min_downloads: int = 10
    min_favorites: int = 1
    min_pages: int = 50
    page_bonus_threshold: int = 200
    page_bonus_points: int = 10
    academic_patterns: List[str] = field(default_factory=list)
    academic_penalty: int = 40
    interview_patterns: List[str] = field(default_factory=list)
    interview_penalty: int = 50
    live_recording_patterns: List[str] = field(default_factory=list)
    live_recording_penalty: int = 30
    trusted_publishers: List[str] = field(default_factory=list)
    publisher_bonus: int = 15
    trusted_collections: List[str] = field(default_factory=list)
    collection_bonus: int = 10
    preferred_formats: Dict[str, List[str]] = field(default_factory=dict)
    format_bonus: int = 5

    @classmethod
    def from_yaml(cls, config: dict) -> "FilterConfig":
        """Create FilterConfig from parsed YAML config."""
        return cls(
            min_confidence=config.get("min_confidence", 60),
            min_downloads=config.get("min_downloads", 10),
            min_favorites=config.get("min_favorites", 1),
            min_pages=config.get("page_count", {}).get("min_pages", 50),
            page_bonus_threshold=config.get("page_count", {}).get("bonus_threshold", 200),
            page_bonus_points=config.get("page_count", {}).get("bonus_points", 10),
            academic_patterns=config.get("academic_patterns", []),
            academic_penalty=config.get("academic_penalty", 40),
            interview_patterns=config.get("interview_patterns", []),
            interview_penalty=config.get("interview_penalty", 50),
            live_recording_patterns=config.get("live_recording_patterns", []),
            live_recording_penalty=config.get("live_recording_penalty", 30),
            trusted_publishers=config.get("trusted_publishers", []),
            publisher_bonus=config.get("publisher_bonus", 15),
            trusted_collections=config.get("trusted_collections", []),
            collection_bonus=config.get("collection_bonus", 10),
            preferred_formats=config.get("preferred_formats", {}),
            format_bonus=config.get("format_bonus", 5),
        )


@dataclass
class ConfidenceResult:
    """Result of confidence scoring."""
    score: int
    reasons: List[str]
    passes: bool

    def __str__(self) -> str:
        status = "PASS" if self.passes else "FAIL"
        reasons_str = "; ".join(self.reasons) if self.reasons else "No adjustments"
        return f"[{status}] Score: {self.score} - {reasons_str}"


def calculate_confidence(
    item: dict,
    metadata: Optional[dict],
    mediatype: str,
    config: FilterConfig
) -> ConfidenceResult:
    """Calculate confidence score for an item.

    Args:
        item: Basic item info from search
        metadata: Full metadata (if fetched) or None
        mediatype: The mediatype being searched for
        config: Filter configuration

    Returns:
        ConfidenceResult with score, reasons, and pass/fail status
    """
    score = 70  # Base score
    reasons = []

    # Combine title and description for text analysis
    title = item.get("title") or ""
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)
    title = title.lower()

    description = item.get("description") or ""
    if isinstance(description, list):
        description = " ".join(str(d) for d in description)
    description = description.lower()

    text = f"{title} {description}"

    # --- Page count check (for texts) ---
    if mediatype == "texts" and metadata:
        page_count = metadata.get("_page_count")
        if page_count is not None:
            if page_count < config.min_pages:
                penalty = 25
                score -= penalty
                reasons.append(f"-{penalty}: Only {page_count} pages (min: {config.min_pages})")
            elif page_count >= config.page_bonus_threshold:
                score += config.page_bonus_points
                reasons.append(f"+{config.page_bonus_points}: {page_count} pages (substantial work)")

    # --- Academic paper detection ---
    for pattern in config.academic_patterns:
        if pattern.lower() in text:
            score -= config.academic_penalty
            reasons.append(f"-{config.academic_penalty}: Academic pattern detected: '{pattern}'")
            break  # Only penalize once

    # --- Interview/live recording detection (for audio) ---
    if mediatype == "audio":
        for pattern in config.interview_patterns:
            if pattern.lower() in text:
                score -= config.interview_penalty
                reasons.append(f"-{config.interview_penalty}: Interview pattern: '{pattern}'")
                break

        for pattern in config.live_recording_patterns:
            if pattern.lower() in text:
                score -= config.live_recording_penalty
                reasons.append(f"-{config.live_recording_penalty}: Live recording pattern: '{pattern}'")
                break

    # --- Publisher bonus ---
    publisher = item.get("publisher") or ""
    if isinstance(publisher, list):
        publisher = " ".join(publisher)
    publisher_lower = publisher.lower()

    for trusted in config.trusted_publishers:
        if trusted.lower() in publisher_lower:
            score += config.publisher_bonus
            reasons.append(f"+{config.publisher_bonus}: Trusted publisher: {trusted}")
            break

    # --- Collection bonus ---
    collections = item.get("collection") or []
    if isinstance(collections, str):
        collections = [collections]

    for coll in collections:
        if coll in config.trusted_collections:
            score += config.collection_bonus
            reasons.append(f"+{config.collection_bonus}: Trusted collection: {coll}")
            break

    # --- Format bonus ---
    if metadata and "_files" in metadata:
        preferred = config.preferred_formats.get(mediatype, [])
        for file_info in metadata["_files"]:
            file_format = file_info.get("format", "")
            if file_format in preferred:
                score += config.format_bonus
                reasons.append(f"+{config.format_bonus}: Preferred format: {file_format}")
                break

    # --- Download count bonus (popularity indicator) ---
    downloads = item.get("downloads", 0)
    if downloads and downloads > 1000:
        bonus = min(10, downloads // 1000)
        score += bonus
        reasons.append(f"+{bonus}: Popular ({downloads} downloads)")

    # Clamp score to 0-100
    score = max(0, min(100, score))

    return ConfidenceResult(
        score=score,
        reasons=reasons,
        passes=score >= config.min_confidence
    )


def matches_search_intent(item: dict, search_name: str) -> bool:
    """Check if an item actually matches the search intent.

    Helps filter out false positives from broad searches.

    Args:
        item: Item dict with title, creator, etc.
        search_name: The original search term name

    Returns:
        True if item appears to match the intent
    """
    title = (item.get("title") or "").lower()
    creator = item.get("creator") or ""
    if isinstance(creator, list):
        creator = " ".join(creator)
    creator = creator.lower()

    search_lower = search_name.lower()
    search_words = search_lower.split()

    # Check if any significant word from search appears in title or creator
    for word in search_words:
        if len(word) > 3:  # Skip short words
            if word in title or word in creator:
                return True

    return False


def meets_engagement_threshold(item: dict, config: FilterConfig) -> tuple[bool, str]:
    """Check if an item meets minimum engagement thresholds.

    Args:
        item: Item dict with downloads, num_favorites, etc.
        config: Filter configuration with min_downloads, min_favorites

    Returns:
        Tuple of (passes, reason) - reason explains why it failed if applicable
    """
    downloads = item.get("downloads", 0) or 0
    favorites = item.get("num_favorites", 0) or 0

    if downloads < config.min_downloads:
        return False, f"Downloads ({downloads}) below minimum ({config.min_downloads})"

    if favorites < config.min_favorites:
        return False, f"Favorites ({favorites}) below minimum ({config.min_favorites})"

    return True, ""
