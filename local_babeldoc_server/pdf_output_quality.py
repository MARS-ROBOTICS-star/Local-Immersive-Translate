from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_RENDER_SCALE = 0.5
DEFAULT_PIXEL_DELTA = 12
DEFAULT_CHANGED_PIXEL_FRACTION = 0.02
DEFAULT_MEAN_ABSOLUTE_DIFFERENCE = 1.0
GEOMETRY_TOLERANCE_POINTS = 1.0


class PdfOutputIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PageRenderComparison:
    page_number: int
    changed_pixel_fraction: float
    mean_absolute_difference: float
    source_width: int
    source_height: int


@dataclass(frozen=True, slots=True)
class PdfOutputAudit:
    passed: bool
    page_count: int
    pages: tuple[PageRenderComparison, ...]
    failed_pages: tuple[int, ...]
    failure_reason: str = ""


def _render_gray(page, clip, scale: float) -> tuple[bytes, int, int]:
    import pymupdf

    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        colorspace=pymupdf.csGRAY,
        alpha=False,
        clip=clip,
    )
    return bytes(pixmap.samples), pixmap.width, pixmap.height


def _compare_samples(
    source_samples: bytes,
    dual_samples: bytes,
    pixel_delta: int,
) -> tuple[float, float]:
    if len(source_samples) != len(dual_samples) or not source_samples:
        return 1.0, 255.0
    changed = 0
    absolute_difference = 0
    for source_value, dual_value in zip(source_samples, dual_samples):
        difference = abs(source_value - dual_value)
        absolute_difference += difference
        if difference > pixel_delta:
            changed += 1
    sample_count = len(source_samples)
    return changed / sample_count, absolute_difference / sample_count


def audit_side_by_side_source(
    source_path: str | Path,
    dual_path: str | Path,
    mode: str,
    *,
    render_scale: float = DEFAULT_RENDER_SCALE,
    pixel_delta: int = DEFAULT_PIXEL_DELTA,
    changed_pixel_fraction: float = DEFAULT_CHANGED_PIXEL_FRACTION,
    mean_absolute_difference: float = DEFAULT_MEAN_ABSOLUTE_DIFFERENCE,
) -> PdfOutputAudit:
    import pymupdf

    normalized_mode = (mode or "").casefold()
    if normalized_mode not in {"lort", "ltro"}:
        raise ValueError(
            "source-render audit supports only side-by-side modes: lort, ltro"
        )

    source = pymupdf.open(str(source_path))
    dual = pymupdf.open(str(dual_path))
    try:
        source_page_count = len(source)
        if len(dual) != source_page_count:
            return PdfOutputAudit(
                passed=False,
                page_count=source_page_count,
                pages=(),
                failed_pages=(),
                failure_reason="page_count_mismatch",
            )

        pages = []
        failed_pages = []
        for page_index in range(source_page_count):
            source_page = source[page_index]
            dual_page = dual[page_index]
            source_rect = source_page.rect
            dual_rect = dual_page.rect
            half_width = dual_rect.width / 2.0
            geometry_matches = (
                abs(half_width - source_rect.width)
                <= GEOMETRY_TOLERANCE_POINTS
                and abs(dual_rect.height - source_rect.height)
                <= GEOMETRY_TOLERANCE_POINTS
            )
            if not geometry_matches:
                comparison = PageRenderComparison(
                    page_number=page_index + 1,
                    changed_pixel_fraction=1.0,
                    mean_absolute_difference=255.0,
                    source_width=0,
                    source_height=0,
                )
                pages.append(comparison)
                failed_pages.append(page_index + 1)
                continue

            if normalized_mode == "lort":
                dual_source_rect = pymupdf.Rect(
                    dual_rect.x0,
                    dual_rect.y0,
                    dual_rect.x0 + half_width,
                    dual_rect.y1,
                )
            else:
                dual_source_rect = pymupdf.Rect(
                    dual_rect.x0 + half_width,
                    dual_rect.y0,
                    dual_rect.x1,
                    dual_rect.y1,
                )

            source_samples, source_width, source_height = _render_gray(
                source_page,
                source_rect,
                render_scale,
            )
            dual_samples, dual_width, dual_height = _render_gray(
                dual_page,
                dual_source_rect,
                render_scale,
            )
            if (source_width, source_height) != (dual_width, dual_height):
                changed_fraction = 1.0
                mean_difference = 255.0
            else:
                changed_fraction, mean_difference = _compare_samples(
                    source_samples,
                    dual_samples,
                    pixel_delta,
                )
            comparison = PageRenderComparison(
                page_number=page_index + 1,
                changed_pixel_fraction=changed_fraction,
                mean_absolute_difference=mean_difference,
                source_width=source_width,
                source_height=source_height,
            )
            pages.append(comparison)
            if (
                changed_fraction > changed_pixel_fraction
                and mean_difference > mean_absolute_difference
            ):
                failed_pages.append(page_index + 1)

        return PdfOutputAudit(
            passed=not failed_pages,
            page_count=source_page_count,
            pages=tuple(pages),
            failed_pages=tuple(failed_pages),
            failure_reason="source_render_mismatch" if failed_pages else "",
        )
    finally:
        dual.close()
        source.close()


def ensure_side_by_side_source_preserved(audit: PdfOutputAudit) -> None:
    if audit.passed:
        return
    if audit.failure_reason == "page_count_mismatch":
        raise PdfOutputIntegrityError(
            "Dual PDF page count does not match the source PDF page count"
        )
    if audit.failed_pages:
        page_number = audit.failed_pages[0]
        comparison = next(
            page for page in audit.pages if page.page_number == page_number
        )
        raise PdfOutputIntegrityError(
            "Dual PDF source rendering differs from the source on "
            f"page {page_number}: "
            f"changed={comparison.changed_pixel_fraction:.4f}, "
            f"mean_delta={comparison.mean_absolute_difference:.2f}"
        )
    raise PdfOutputIntegrityError("Dual PDF source rendering audit failed")
