import hashlib
import json
import pytest
import sys
from pathlib import Path
import pymupdf as fitz

# Add directory to sys.path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from pdf_prepare import (
    parse_args,
    validate_and_open_pdf,
    validate_page_selection,
    resolve_target_pages,
    extract_page_text,
    check_render_pixel_bounds,
    prepare_pdf,
    main,
    PDFPrepareError,
)




def test_cli_rejects_output_root_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PDFPrepareError, match="Symlink"):
        parse_args(["sample.pdf", "--output-root", str(link)])


def test_rejects_oversized_range_before_expansion():
    from pdf_prepare import parse_page_spec
    with pytest.raises(PDFPrepareError, match="Cannot select more than 3"):
        parse_page_spec("1-10000")


def test_manifest_rejects_path_escape(tmp_path):
    from pdf_prepare import validate_manifest
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "manifest.json").write_text(json.dumps({
        "pdf_sha256": "hash", "pages": [1], "dpi": 150, "render": False,
        "files": {"../outside.txt": {"size": 7, "sha256": hashlib.sha256(b"outside").hexdigest()}},
        "pages_info": []
    }))
    assert validate_manifest(cache, "hash", [1], 150, False) is None


def test_parse_args_rejects_relative_output_root():
    with pytest.raises(PDFPrepareError, match="--output-root must be an absolute path"):
        parse_args(["sample.pdf", "--output-root", "relative/path"])


def test_parse_args_rejects_dpi_greater_than_200():
    with pytest.raises(PDFPrepareError, match="DPI cannot exceed 200"):
        parse_args(["sample.pdf", "--output-root", "/tmp/out", "--dpi", "250"])


def test_parse_args_rejects_dpi_less_than_1():
    with pytest.raises(PDFPrepareError, match="DPI must be at least 1"):
        parse_args(["sample.pdf", "--output-root", "/tmp/out", "--dpi", "0"])


def test_parse_args_rejects_more_than_3_pages():
    with pytest.raises(PDFPrepareError, match="Cannot select more than 3 pages"):
        parse_args(["sample.pdf", "--output-root", "/tmp/out", "--pages", "1,2,3,4"])


def test_parse_args_valid_defaults():
    args = parse_args(["sample.pdf", "--output-root", "/tmp/out"])
    assert args.pdf_path == Path("sample.pdf").resolve()
    assert args.output_root == Path("/tmp/out").resolve()
    assert args.dpi == 150
    assert args.raw_pages_spec is None
    assert args.render is False


def test_reject_nonexistent_file(tmp_path):
    missing_file = tmp_path / "nonexistent.pdf"
    with pytest.raises(PDFPrepareError, match="File not found"):
        validate_and_open_pdf(missing_file)


def test_reject_file_exceeding_30mib(tmp_path):
    big_file = tmp_path / "too_big.pdf"
    with open(big_file, "wb") as f:
        f.seek(30 * 1024 * 1024)
        f.write(b"x")
    with pytest.raises(PDFPrepareError, match="File size exceeds maximum allowed 30MiB"):
        validate_and_open_pdf(big_file)


def test_reject_non_pdf_file(tmp_path):
    garbage = tmp_path / "not_a_pdf.pdf"
    garbage.write_bytes(b"This is plain text, not a valid PDF file at all.")
    with pytest.raises(PDFPrepareError, match="Failed to parse PDF"):
        validate_and_open_pdf(garbage)


def test_reject_encrypted_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Confidential data")
    pdf_path = tmp_path / "encrypted.pdf"
    doc.save(
        str(pdf_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner123",
        user_pw="user123",
    )
    doc.close()

    with pytest.raises(PDFPrepareError, match="Encrypted or password-protected PDF is not supported"):
        validate_and_open_pdf(pdf_path)


def test_reject_pdf_with_more_than_500_pages(tmp_path):
    doc = fitz.open()
    for _ in range(501):
        doc.new_page()
    pdf_path = tmp_path / "large_pages.pdf"
    doc.save(str(pdf_path))
    doc.close()

    with pytest.raises(PDFPrepareError, match="exceeds maximum allowed 500 pages"):
        validate_and_open_pdf(pdf_path)


def test_reject_out_of_range_page_selection(tmp_path):
    doc = fitz.open()
    doc.new_page()
    doc.new_page()  # 2 pages total
    pdf_path = tmp_path / "two_pages.pdf"
    doc.save(str(pdf_path))
    doc.close()

    doc, _, _ = validate_and_open_pdf(pdf_path)
    try:
        with pytest.raises(PDFPrepareError, match="Page 3 is out of range"):
            resolve_target_pages("1,3", total_pages=len(doc))
    finally:
        doc.close()


def test_resolve_default_pages_for_fewer_than_3_pages():
    assert resolve_target_pages(None, total_pages=1) == [1]
    assert resolve_target_pages(None, total_pages=2) == [1, 2]
    assert resolve_target_pages(None, total_pages=10) == [1, 2, 3]


def test_valid_pdf_loads_and_hashes(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Hello TDD World")
    pdf_path = tmp_path / "valid.pdf"
    doc.save(str(pdf_path))
    doc.close()

    opened_doc, raw_bytes, sha256_hex = validate_and_open_pdf(pdf_path)
    try:
        assert len(opened_doc) == 1
        assert sha256_hex == hashlib.sha256(raw_bytes).hexdigest()
        assert len(raw_bytes) == pdf_path.stat().st_size
    finally:
        opened_doc.close()


def test_extract_page_text_bounds_to_32000_chars():
    doc = fitz.open()
    p = doc.new_page(width=2000, height=20000)
    huge_text = "word " * 8000  # 40,000 chars
    p.insert_textbox(fitz.Rect(0, 0, 2000, 20000), huge_text, fontsize=6)
    
    extracted, has_useful_text = extract_page_text(p)
    assert len(extracted) == 32000
    assert has_useful_text is True
    doc.close()


def test_extract_page_text_identifies_useful_text_threshold():
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "  Short note!  ")  # < 40 chars
    text1, useful1 = extract_page_text(p1)
    assert useful1 is False

    p2 = doc.new_page()
    p2.insert_text((50, 50), "This is a document page containing enough text to be useful.")
    text2, useful2 = extract_page_text(p2)
    assert useful2 is True
    doc.close()


def test_reject_page_exceeding_16m_pixels_before_render():
    doc = fitz.open()
    p = doc.new_page(width=3000, height=3000)
    with pytest.raises(PDFPrepareError, match="exceeds maximum allowed 16,000,000"):
        check_render_pixel_bounds(p, dpi=200)
    doc.close()


def test_allow_page_within_16m_pixels():
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    w, h, total = check_render_pixel_bounds(p, dpi=150)
    assert total < 16_000_000
    assert w > 0 and h > 0
    doc.close()


def test_prepare_pdf_text_page_does_not_render_without_flag(tmp_path):
    # Synthetic PDF with useful text on page 1
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "This is page 1 with more than forty characters of useful extracted text.")
    pdf_path = tmp_path / "text_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cache_root"
    res = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=False)

    assert res["cache_hit"] is False
    assert res["total_pages"] == 1
    assert res["selected_pages"] == [1]
    assert len(res["pages"]) == 1
    page1 = res["pages"][0]
    assert page1["has_useful_text"] is True
    assert page1["rendered"] is False
    assert page1["image_path"] is None
    assert Path(page1["text_path"]).is_file()
    assert "useful extracted text" in Path(page1["text_path"]).read_text(encoding="utf-8")


def test_prepare_pdf_image_only_page_renders_png(tmp_path):
    # Synthetic PDF with drawing / no useful text
    doc = fitz.open()
    p = doc.new_page()
    # Draw a line or circle (diagram / scan without text)
    p.draw_circle(fitz.Point(100, 100), 50, color=(1, 0, 0), fill=(0, 1, 0))
    pdf_path = tmp_path / "drawing_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cache_root"
    res = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=False)

    assert res["cache_hit"] is False
    page1 = res["pages"][0]
    assert page1["has_useful_text"] is False
    assert page1["rendered"] is True
    assert page1["image_path"] is not None
    assert Path(page1["image_path"]).is_file()
    assert Path(page1["image_path"]).suffix == ".png"


def test_prepare_pdf_render_flag_forces_rendering_even_with_useful_text(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "This page has lots of text, but we want to inspect diagrams on it.")
    pdf_path = tmp_path / "diagram_text.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cache_root"
    res = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=True)

    page1 = res["pages"][0]
    assert page1["has_useful_text"] is True
    assert page1["rendered"] is True
    assert page1["image_path"] is not None
    assert Path(page1["image_path"]).is_file()


def test_prepare_pdf_cache_hit_and_manifest_validation(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Testing content addressed caching mechanism.")
    pdf_path = tmp_path / "cache_test.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cache_root"
    # First invocation -> cache miss
    res1 = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=False)
    assert res1["cache_hit"] is False

    # Second invocation -> cache hit
    res2 = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=False)
    assert res2["cache_hit"] is True
    assert res2["artifact_dir"] == res1["artifact_dir"]

    # Now tamper with text file in artifact dir -> next call must detect mismatch and regenerate (cache_hit=False)
    text_path = Path(res1["pages"][0]["text_path"])
    text_path.write_text("Tampered content!", encoding="utf-8")

    with pytest.raises(PDFPrepareError, match="Invalid cache"):
        prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150, render=False)
    assert text_path.read_text() == "Tampered content!"


def test_prepare_pdf_rejects_symlinks_in_output_root(tmp_path):
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    symlink_root = tmp_path / "symlink_root"
    symlink_root.symlink_to(real_target, target_is_directory=True)

    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Testing symlink protection")
    pdf_path = tmp_path / "symlink_test.pdf"
    doc.save(str(pdf_path))
    doc.close()

    with pytest.raises(PDFPrepareError, match="Symlink detected in output"):
        prepare_pdf(pdf_path=pdf_path, output_root=symlink_root, pages_spec="1", dpi=150)


def test_prepare_pdf_rejects_symlink_inside_artifact_dir(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Testing descendant symlink protection")
    pdf_path = tmp_path / "symlink_desc_test.pdf"
    doc.save(str(pdf_path))
    doc.close()

    _, raw_bytes, pdf_sha256 = validate_and_open_pdf(pdf_path)
    out_root = tmp_path / "safe_root"
    artifact_dir = out_root / pdf_sha256 / "pages_1_dpi_150_render_0"
    artifact_dir.mkdir(parents=True)
    bad_link = artifact_dir / "evil_link.txt"
    bad_link.symlink_to(pdf_path)

    with pytest.raises(PDFPrepareError, match="Symlink detected in output"):
        prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec="1", dpi=150)


def test_unprocessed_pages_note(tmp_path):
    doc = fitz.open()
    for i in range(5):
        p = doc.new_page()
        p.insert_text((50, 50), f"Page {i + 1} content text exceeding forty characters threshold.")
    pdf_path = tmp_path / "five_pages.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cache_root"
    # Select default first 3 pages
    res = prepare_pdf(pdf_path=pdf_path, output_root=out_root, pages_spec=None, dpi=150)
    assert res["total_pages"] == 5
    assert res["selected_pages"] == [1, 2, 3]
    assert "Pages 4-5" in res["unprocessed_note"] or "Unprocessed pages" in res["unprocessed_note"]


def test_cli_main_success(tmp_path, capsys):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Sample document for testing CLI interface execution output.")
    pdf_path = tmp_path / "cli_sample.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cli_out"
    exit_code = main([str(pdf_path), "--output-root", str(out_root), "--dpi", "150"])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["total_pages"] == 1
    assert payload["selected_pages"] == [1]
    assert payload["cache_hit"] is False
    assert len(payload["pages"]) == 1
    assert payload["pages"][0]["has_useful_text"] is True


def test_cli_main_error_exit_code(capsys):
    exit_code = main(["nonexistent.pdf", "--output-root", "/tmp/any"])
    assert exit_code == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert "error" in payload
    assert "File not found" in payload["error"]


def test_cli_explicit_pages_selection(tmp_path, capsys):
    doc = fitz.open()
    for i in range(4):
        p = doc.new_page()
        p.insert_text((50, 50), f"This is page {i + 1} with more than forty chars of text content.")
    pdf_path = tmp_path / "cli_pages.pdf"
    doc.save(str(pdf_path))
    doc.close()

    out_root = tmp_path / "cli_out_pages"
    exit_code = main([str(pdf_path), "--output-root", str(out_root), "--pages", "2,4"])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["selected_pages"] == [2, 4]
    assert payload["total_pages"] == 4
    pages = payload["pages"]
    assert len(pages) == 2
    assert pages[0]["page"] == 2
    assert pages[1]["page"] == 4

