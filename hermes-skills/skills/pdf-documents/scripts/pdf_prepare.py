#!/usr/bin/env python3
"""Safe PDF preparation utility for personal AI assistants."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple
import pymupdf as fitz

MAX_INPUT_BYTES = 30 * 1024 * 1024  # 30 MiB
MAX_TOTAL_PAGES = 500
MAX_SELECTED_PAGES = 3
MAX_CHARS_PER_PAGE = 32000
MAX_PIXELS_PER_PAGE = 16_000_000
USEFUL_TEXT_CHAR_THRESHOLD = 40


class PDFPrepareError(Exception):
    """Base exception for PDF preparation errors."""
    pass


def parse_page_spec(page_spec: str) -> List[int]:
    """Parse a non-empty page specification string like '1,3-4' into a sorted list of 1-based integers."""
    pages = set()
    parts = page_spec.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            try:
                start = int(start_str.strip())
                end = int(end_str.strip())
            except ValueError:
                raise PDFPrepareError(f"Invalid page range: '{part}'")
            if start < 1 or end < 1 or start > end:
                raise PDFPrepareError(f"Invalid page range: '{part}'")
            if end - start + 1 > MAX_SELECTED_PAGES:
                raise PDFPrepareError("Cannot select more than 3 pages per invocation")
            pages.update(range(start, end + 1))
        else:
            try:
                p = int(part)
            except ValueError:
                raise PDFPrepareError(f"Invalid page number: '{part}'")
            if p < 1:
                raise PDFPrepareError(f"Page numbers must be >= 1: {p}")
            pages.add(p)
            
    sorted_pages = sorted(pages)
    if not sorted_pages:
        raise PDFPrepareError("No valid pages specified")
    if len(sorted_pages) > MAX_SELECTED_PAGES:
        raise PDFPrepareError(f"Cannot select more than {MAX_SELECTED_PAGES} pages per invocation (requested {len(sorted_pages)})")
    return sorted_pages


def resolve_target_pages(pages_spec: Optional[str], total_pages: int) -> List[int]:
    """Resolve target pages based on user spec or default first 3, validating document bounds."""
    if pages_spec is None:
        count = min(total_pages, MAX_SELECTED_PAGES)
        return list(range(1, count + 1))
    
    pages = parse_page_spec(pages_spec)
    for p in pages:
        if p < 1 or p > total_pages:
            raise PDFPrepareError(f"Page {p} is out of range for document with {total_pages} pages")
    return pages


def validate_page_selection(pages: List[int], total_pages: int) -> List[int]:
    """Validate that page selection does not exceed max selected pages or total document pages."""
    if len(pages) > MAX_SELECTED_PAGES:
        raise PDFPrepareError(f"Cannot select more than {MAX_SELECTED_PAGES} pages per invocation (requested {len(pages)})")

    for p in pages:
        if p < 1 or p > total_pages:
            raise PDFPrepareError(f"Page {p} is out of range for document with {total_pages} pages")

    return sorted(list(set(pages)))


def validate_and_open_pdf(pdf_path: Path) -> Tuple[fitz.Document, bytes, str]:
    """Validate file existence, size, read bytes once, compute SHA-256 and open fitz.Document."""
    if not pdf_path.exists():
        raise PDFPrepareError(f"File not found: '{pdf_path}'")
    if not pdf_path.is_file():
        raise PDFPrepareError(f"Target is not a regular file: '{pdf_path}'")

    stat = pdf_path.stat()
    if stat.st_size > MAX_INPUT_BYTES:
        raise PDFPrepareError(f"File size exceeds maximum allowed 30MiB: {stat.st_size} bytes")

    try:
        raw_bytes = pdf_path.read_bytes()
    except Exception as e:
        raise PDFPrepareError(f"Failed to read file '{pdf_path}': {e}")

    if len(raw_bytes) > MAX_INPUT_BYTES:
        raise PDFPrepareError(f"File size exceeds maximum allowed 30MiB: {len(raw_bytes)} bytes")

    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

    try:
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
    except Exception as e:
        raise PDFPrepareError(f"Failed to parse PDF document '{pdf_path}': {e}")

    if doc.is_encrypted or doc.needs_pass:
        doc.close()
        raise PDFPrepareError(f"Encrypted or password-protected PDF is not supported: '{pdf_path}'")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise PDFPrepareError(f"PDF document has 0 pages: '{pdf_path}'")
    if total_pages > MAX_TOTAL_PAGES:
        doc.close()
        raise PDFPrepareError(f"PDF page count ({total_pages}) exceeds maximum allowed {MAX_TOTAL_PAGES} pages")

    return doc, raw_bytes, sha256_hex


def extract_page_text(page: fitz.Page) -> Tuple[str, bool]:
    """Extract page text bounded to MAX_CHARS_PER_PAGE and determine if it has useful text."""
    full_text = page.get_text("text")
    if len(full_text) > MAX_CHARS_PER_PAGE:
        bounded_text = full_text[:MAX_CHARS_PER_PAGE]
    else:
        bounded_text = full_text

    stripped = bounded_text.strip()
    has_useful_text = len(stripped) >= USEFUL_TEXT_CHAR_THRESHOLD
    return bounded_text, has_useful_text


def check_render_pixel_bounds(page: fitz.Page, dpi: int) -> Tuple[int, int, int]:
    """Calculate and validate pixel dimensions before rendering."""
    scale = dpi / 72.0
    rect = page.rect
    import math
    width = math.ceil(rect.width * scale)
    height = math.ceil(rect.height * scale)
    total_pixels = width * height

    if total_pixels > MAX_PIXELS_PER_PAGE:
        raise PDFPrepareError(
            f"Page render pixel count ({total_pixels}) exceeds maximum allowed 16,000,000 "
            f"(dimensions: {width}x{height} at {dpi} DPI)"
        )
    return width, height, total_pixels


def check_no_symlinks(path: Path, base_root: Path) -> None:
    """Verify that path and none of its ancestors down to base_root is a symlink."""
    curr = path
    while True:
        if curr.is_symlink():
            raise PDFPrepareError(f"Symlink detected in output path: '{curr}'")
        if curr == base_root or curr.parent == curr:
            break
        curr = curr.parent
    if base_root.is_symlink():
        raise PDFPrepareError(f"Symlink detected in output path: '{base_root}'")


def check_tree_has_no_symlinks(root_dir: Path) -> None:
    """Recursively check that no file or directory inside root_dir is a symlink."""
    if not root_dir.exists():
        return
    if root_dir.is_symlink():
        raise PDFPrepareError(f"Symlink detected in output path: '{root_dir}'")
    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False):
        for d in dirnames:
            p = Path(dirpath) / d
            if p.is_symlink():
                raise PDFPrepareError(f"Symlink detected in output tree: '{p}'")
        for f in filenames:
            p = Path(dirpath) / f
            if p.is_symlink():
                raise PDFPrepareError(f"Symlink detected in output tree: '{p}'")


def format_unprocessed_note(total_pages: int, selected_pages: List[int]) -> str:
    unprocessed = sorted(list(set(range(1, total_pages + 1)) - set(selected_pages)))
    if not unprocessed:
        return f"All {total_pages} page(s) processed."

    # Group ranges
    ranges = []
    start = unprocessed[0]
    prev = start
    for p in unprocessed[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = p
            prev = p
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    pages_str = ", ".join(ranges)
    return f"Pages {pages_str} were not processed (limit 3 pages per run). Call again with --pages to inspect specific pages."


def validate_manifest(artifact_dir: Path, expected_hash: str, expected_pages: List[int], dpi: int, render: bool) -> Optional[Dict[str, Any]]:
    """Validate existing cached manifest and referenced files. Returns manifest dict if valid, else None."""
    manifest_file = artifact_dir / "manifest.json"
    if not manifest_file.exists() or manifest_file.is_symlink() or not manifest_file.is_file():
        return None

    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    if data.get("pdf_sha256") != expected_hash:
        return None
    if data.get("pages") != expected_pages:
        return None
    if data.get("dpi") != dpi:
        return None
    if data.get("render") != render:
        return None

    files = data.get("files", {})
    if not isinstance(files, dict):
        return None

    page_infos = data.get("pages_info")
    if not isinstance(page_infos, list) or len(page_infos) != len(expected_pages):
        return None
    expected_files = set()
    for page_number, entry in zip(expected_pages, page_infos):
        if not isinstance(entry, dict) or entry.get("page") != page_number:
            return None
        if entry.get("text_filename") != f"page_{page_number}.txt":
            return None
        expected_files.add(entry["text_filename"])
        if entry.get("image_filename") is not None:
            if entry["image_filename"] != f"page_{page_number}.png":
                return None
            expected_files.add(entry["image_filename"])
    if set(files) != expected_files:
        return None
    for fname, info in files.items():
        if not isinstance(info, dict) or Path(fname).name != fname:
            return None
        fpath = artifact_dir / fname
        if not fpath.exists() or fpath.is_symlink() or not fpath.is_file():
            return None
        expected_size = info.get("size")
        expected_sha = info.get("sha256")
        if fpath.stat().st_size != expected_size:
            return None
        content = fpath.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha:
            return None

    return data


def prepare_pdf(
    pdf_path: Path,
    output_root: Path,
    pages_spec: Optional[str] = None,
    dpi: int = 150,
    render: bool = False,
) -> Dict[str, Any]:
    """Safely prepare PDF: extract text and render PNGs where necessary."""
    if not output_root.is_absolute():
        raise PDFPrepareError(f"--output-root must be an absolute path: '{output_root}'")

    check_no_symlinks(output_root, Path(output_root.anchor))

    doc, _, pdf_sha256 = validate_and_open_pdf(pdf_path)
    try:
        total_pages = len(doc)
        target_pages = resolve_target_pages(pages_spec, total_pages=total_pages)
        unprocessed_note = format_unprocessed_note(total_pages, target_pages)

        pages_str = "_".join(str(p) for p in target_pages)
        settings_name = f"pages_{pages_str}_dpi_{dpi}_render_{1 if render else 0}"
        hash_dir = output_root / pdf_sha256
        artifact_dir = hash_dir / settings_name

        check_no_symlinks(hash_dir, output_root)
        check_no_symlinks(artifact_dir, output_root)
        if artifact_dir.exists():
            check_tree_has_no_symlinks(artifact_dir)

        # Check existing cache
        if artifact_dir.exists():
            manifest_data = validate_manifest(artifact_dir, pdf_sha256, target_pages, dpi, render)
            if manifest_data is not None:
                pages_result = []
                for p_info in manifest_data["pages_info"]:
                    text_p = str(artifact_dir / p_info["text_filename"])
                    img_p = str(artifact_dir / p_info["image_filename"]) if p_info.get("image_filename") else None
                    pages_result.append({
                        "page": p_info["page"],
                        "char_count": p_info["char_count"],
                        "has_useful_text": p_info["has_useful_text"],
                        "rendered": p_info["rendered"],
                        "text_path": text_p,
                        "image_path": img_p,
                    })

                return {
                    "pdf_path": str(pdf_path),
                    "pdf_sha256": pdf_sha256,
                    "total_pages": total_pages,
                    "selected_pages": target_pages,
                    "cache_hit": True,
                    "artifact_dir": str(artifact_dir),
                    "manifest_path": str(artifact_dir / "manifest.json"),
                    "unprocessed_note": unprocessed_note,
                    "pages": pages_result,
                }

        if artifact_dir.exists():
            raise PDFPrepareError("Invalid cache: preserve existing artifacts; use a new output root or ask administrator")

        # Cache miss: generate artifacts atomically
        hash_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=".tmp_", dir=hash_dir))

        try:
            files_meta = {}
            pages_info = []
            pages_result = []

            for p in target_pages:
                page_obj = doc.load_page(p - 1)
                text_content, has_useful_text = extract_page_text(page_obj)

                text_filename = f"page_{p}.txt"
                text_target = temp_dir / text_filename
                text_bytes = text_content.encode("utf-8")
                text_target.write_bytes(text_bytes)

                files_meta[text_filename] = {
                    "size": len(text_bytes),
                    "sha256": hashlib.sha256(text_bytes).hexdigest(),
                }

                must_render = render or (not has_useful_text)
                image_filename = None

                if must_render:
                    check_render_pixel_bounds(page_obj, dpi)
                    image_filename = f"page_{p}.png"
                    image_target = temp_dir / image_filename
                    pix = page_obj.get_pixmap(dpi=dpi)
                    pix.save(str(image_target))
                    img_bytes = image_target.read_bytes()
                    files_meta[image_filename] = {
                        "size": len(img_bytes),
                        "sha256": hashlib.sha256(img_bytes).hexdigest(),
                    }

                p_entry = {
                    "page": p,
                    "char_count": len(text_content),
                    "has_useful_text": has_useful_text,
                    "rendered": must_render,
                    "text_filename": text_filename,
                    "image_filename": image_filename,
                }
                pages_info.append(p_entry)

            manifest_content = {
                "pdf_sha256": pdf_sha256,
                "pages": target_pages,
                "dpi": dpi,
                "render": render,
                "files": files_meta,
                "pages_info": pages_info,
            }
            manifest_target = temp_dir / "manifest.json"
            manifest_bytes = json.dumps(manifest_content, indent=2).encode("utf-8")
            manifest_target.write_bytes(manifest_bytes)

            # Atomic publication to artifact_dir
            if artifact_dir.exists():
                raise PDFPrepareError("Cache appeared concurrently; retry without deleting existing artifacts")

            os.replace(temp_dir, artifact_dir)

            for p_info in pages_info:
                text_p = str(artifact_dir / p_info["text_filename"])
                img_p = str(artifact_dir / p_info["image_filename"]) if p_info.get("image_filename") else None
                pages_result.append({
                    "page": p_info["page"],
                    "char_count": p_info["char_count"],
                    "has_useful_text": p_info["has_useful_text"],
                    "rendered": p_info["rendered"],
                    "text_path": text_p,
                    "image_path": img_p,
                })

            return {
                "pdf_path": str(pdf_path),
                "pdf_sha256": pdf_sha256,
                "total_pages": total_pages,
                "selected_pages": target_pages,
                "cache_hit": False,
                "artifact_dir": str(artifact_dir),
                "manifest_path": str(artifact_dir / "manifest.json"),
                "unprocessed_note": unprocessed_note,
                "pages": pages_result,
            }

        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    finally:
        doc.close()


@dataclass
class CLIArgs:
    pdf_path: Path
    output_root: Path
    raw_pages_spec: Optional[str]
    dpi: int
    render: bool


def parse_args(args_list: Optional[List[str]] = None) -> CLIArgs:
    parser = argparse.ArgumentParser(description="Prepare PDF pages safely for text and vision analysis.")
    parser.add_argument("pdf_path", type=str, help="Path to local PDF file")
    parser.add_argument("--output-root", type=str, required=True, help="Absolute path to cache/output root directory")
    parser.add_argument("--pages", type=str, default=None, help="Pages to process (e.g. '1,3-4', default first 3)")
    parser.add_argument("--dpi", type=int, default=150, help="Rendering DPI (default 150, max 200)")
    parser.add_argument("--render", action="store_true", help="Force rendering page to PNG even if useful text exists")

    parsed = parser.parse_args(args_list)

    output_root_path = Path(parsed.output_root)
    if not output_root_path.is_absolute():
        raise PDFPrepareError(f"--output-root must be an absolute path: '{parsed.output_root}'")

    if parsed.dpi < 1:
        raise PDFPrepareError(f"DPI must be at least 1 (got {parsed.dpi})")
    if parsed.dpi > 200:
        raise PDFPrepareError(f"DPI cannot exceed 200 (got {parsed.dpi})")

    if parsed.pages is not None:
        parse_page_spec(parsed.pages)

    check_no_symlinks(output_root_path, Path(output_root_path.anchor))
    pdf_path = Path(parsed.pdf_path).resolve()

    return CLIArgs(
        pdf_path=pdf_path,
        output_root=output_root_path.resolve(),
        raw_pages_spec=parsed.pages,
        dpi=parsed.dpi,
        render=parsed.render,
    )


def main(args_list: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(args_list)
        result = prepare_pdf(
            pdf_path=args.pdf_path,
            output_root=args.output_root,
            pages_spec=args.raw_pages_spec,
            dpi=args.dpi,
            render=args.render,
        )
        print(json.dumps(result, indent=2))
        return 0
    except PDFPrepareError as e:
        print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        return 1
    except Exception as e:
        print(json.dumps({"error": f"Unexpected error: {e}"}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
