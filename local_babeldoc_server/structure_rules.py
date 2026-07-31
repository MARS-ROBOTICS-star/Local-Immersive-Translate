from __future__ import annotations

import re
import unicodedata
from copy import copy
from dataclasses import dataclass
from typing import Any


REFERENCE_LABEL = "babeldoc_preserve_reference"
TOC_LABEL = "babeldoc_toc_entry"

REFERENCE_HEADINGS = frozenset(
    {
        "references",
        "bibliography",
        "works cited",
        "literature cited",
    }
)
POST_REFERENCE_HEADING_RE = re.compile(
    r"^(?:appendix(?:\s+[a-z0-9]+)?|supplementary material|"
    r"author biographies?|about the authors?)$",
    re.IGNORECASE,
)
TOC_HEADING_RE = re.compile(r"^(?:table of )?contents$", re.IGNORECASE)
TOC_ENTRY_RE = re.compile(
    r"^\s*"
    r"(?P<prefix>(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.?))"
    r"\s+"
    r"(?P<title>.+?)"
    r"\s+"
    r"(?P<leader>(?:\.\s*){2,})"
    r"(?P<page>\d+|[ivxlcdm]+)"
    r"\s*$",
    re.IGNORECASE,
)
TOC_LINE_START_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.?)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TocEntry:
    prefix: str
    title: str
    leader: str
    page_number: str


@dataclass(frozen=True, slots=True)
class StructureStats:
    reference_paragraphs: int = 0
    toc_entries: int = 0


def normalize_heading(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip(":：.。 ").casefold()


def is_reference_heading(text: str) -> bool:
    return normalize_heading(text) in REFERENCE_HEADINGS


def is_post_reference_heading(text: str) -> bool:
    return bool(POST_REFERENCE_HEADING_RE.fullmatch(normalize_heading(text)))


def is_toc_heading(text: str) -> bool:
    return bool(TOC_HEADING_RE.fullmatch(normalize_heading(text)))


def parse_toc_entry(text: str) -> TocEntry | None:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Cf"
    )
    match = TOC_ENTRY_RE.fullmatch(normalized)
    if match is None:
        return None
    return TocEntry(
        prefix=match.group("prefix").rstrip("."),
        title=re.sub(r"\s+", " ", match.group("title")).strip(),
        leader=match.group("leader"),
        page_number=match.group("page"),
    )


def display_width(text: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        for char in text
    )


def rebuild_toc_entry(
    entry: TocEntry,
    translated_title: str,
    target_columns: int,
) -> str:
    title = re.sub(r"\s+", " ", translated_title or "").strip() or entry.title
    fixed = f"{entry.prefix}  {title}"
    reserved_width = display_width(fixed) + display_width(entry.page_number) + 2
    leader_count = max(2, target_columns - reserved_width)
    return f"{fixed} {'.' * leader_count} {entry.page_number}"


def _paragraph_is_at_or_below_heading(paragraph: Any, heading: Any) -> bool:
    if paragraph is heading:
        return True
    paragraph_box = getattr(paragraph, "box", None)
    heading_box = getattr(heading, "box", None)
    if paragraph_box is None or heading_box is None:
        return False
    paragraph_top = getattr(paragraph_box, "y2", None)
    heading_top = getattr(heading_box, "y2", None)
    if paragraph_top is None or heading_top is None:
        return False
    paragraph_left = getattr(paragraph_box, "x", None)
    heading_right = getattr(heading_box, "x2", None)
    if (
        paragraph_left is not None
        and heading_right is not None
        and float(paragraph_left) >= float(heading_right) + 24.0
    ):
        return True
    return float(paragraph_top) <= float(heading_top) + 2.0


def _line_text(composition: Any) -> str | None:
    line = getattr(composition, "pdf_line", None)
    if line is None:
        return None
    return "".join(
        getattr(character, "char_unicode", "") or ""
        for character in getattr(line, "pdf_character", [])
    ).strip()


def _group_toc_line_compositions(paragraph: Any):
    compositions = list(
        getattr(paragraph, "pdf_paragraph_composition", []) or []
    )
    if len(compositions) < 2:
        return []
    groups = []
    current = []
    for composition in compositions:
        text = _line_text(composition)
        if text is None:
            return []
        if TOC_LINE_START_RE.match(text) and current:
            groups.append(current)
            current = []
        current.append((composition, text))
    if current:
        groups.append(current)
    if len(groups) < 2:
        return []

    parsed_groups = []
    for group in groups:
        text = " ".join(part for _, part in group)
        if parse_toc_entry(text) is None:
            return []
        parsed_groups.append((group, text))
    return parsed_groups


def _box_for_compositions(group: list[tuple[Any, str]]):
    boxes = [
        getattr(getattr(composition, "pdf_line", None), "box", None)
        for composition, _ in group
    ]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    result = copy(boxes[0])
    result.x = min(float(box.x) for box in boxes)
    result.y = min(float(box.y) for box in boxes)
    result.x2 = max(float(box.x2) for box in boxes)
    result.y2 = max(float(box.y2) for box in boxes)
    return result


def _split_merged_toc_paragraphs(paragraphs: list[Any]) -> list[Any]:
    result = []
    for paragraph in paragraphs:
        groups = _group_toc_line_compositions(paragraph)
        if not groups:
            result.append(paragraph)
            continue
        for index, (group, text) in enumerate(groups):
            split_paragraph = copy(paragraph)
            split_paragraph.pdf_paragraph_composition = [
                composition for composition, _ in group
            ]
            split_paragraph.unicode = text
            group_box = _box_for_compositions(group)
            if group_box is not None:
                split_paragraph.box = group_box
            debug_id = getattr(paragraph, "debug_id", None)
            if debug_id:
                split_paragraph.debug_id = f"{debug_id}-toc-{index}"
            result.append(split_paragraph)
    return result


def _mark_toc_entries(document: Any) -> int:
    marked = 0
    in_toc = False
    for page in getattr(document, "page", []):
        paragraphs = list(getattr(page, "pdf_paragraph", []))
        has_heading = any(
            is_toc_heading(getattr(paragraph, "unicode", "") or "")
            for paragraph in paragraphs
        )
        if has_heading or in_toc:
            paragraphs = _split_merged_toc_paragraphs(paragraphs)
            page.pdf_paragraph = paragraphs
        entries = [
            paragraph
            for paragraph in paragraphs
            if parse_toc_entry(getattr(paragraph, "unicode", "") or "") is not None
        ]
        if has_heading and len(entries) >= 2:
            in_toc = True
        elif in_toc and not entries:
            in_toc = False
        if not in_toc:
            continue
        for paragraph in entries:
            paragraph.layout_label = TOC_LABEL
            marked += 1
    return marked


def mark_document_structure(document: Any) -> StructureStats:
    reference_count = 0
    in_references = False

    for page in getattr(document, "page", []):
        paragraphs = list(getattr(page, "pdf_paragraph", []))
        if in_references and any(
            is_post_reference_heading(getattr(paragraph, "unicode", "") or "")
            for paragraph in paragraphs
        ):
            in_references = False
            continue

        heading = next(
            (
                paragraph
                for paragraph in paragraphs
                if is_reference_heading(getattr(paragraph, "unicode", "") or "")
            ),
            None,
        )
        if heading is not None:
            in_references = True

        if not in_references:
            continue

        for paragraph in paragraphs:
            should_preserve = heading is None or _paragraph_is_at_or_below_heading(
                paragraph, heading
            )
            if should_preserve:
                paragraph.layout_label = REFERENCE_LABEL
                reference_count += 1

    return StructureStats(
        reference_paragraphs=reference_count,
        toc_entries=_mark_toc_entries(document),
    )
