"""scripts/fetch_data.py: validation and skip logic only (no network access in tests)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_data", ROOT / "scripts" / "fetch_data.py")
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

FRED_RAW = ROOT / "refs" / "fred" / "fred_raw.csv"
PDF = ROOT / "refs" / "bcbs_d457" / "d457_standard.pdf"
TXT = ROOT / "refs" / "bcbs_d457" / "d457.txt"


def test_pdf_validation_rejects_html_landing_page():
    with pytest.raises(ValueError):
        fd.validate_pdf(b"<!DOCTYPE html><html>landing page</html>")
    fd.validate_pdf(b"%PDF-1.6 ...")


def test_fred_validation_checks_header_and_length():
    good = b"observation_date,DGS3MO,DGS1\n" + b"2020-01-01,1.5,1.6\n" * 1200
    fd.validate_fred_csv(good, "DGS3MO,DGS1")
    with pytest.raises(ValueError):
        fd.validate_fred_csv(b"<html>error</html>" + b"\n" * 2000, "DGS3MO,DGS1")
    with pytest.raises(ValueError):
        fd.validate_fred_csv(b"observation_date,DGS3MO\n2020-01-01,1.5\n", "DGS3MO")


def test_urls_and_series():
    assert fd.BCBS_URL.endswith("201901-standards-minimum-capital-requirements-market-risk.pdf")
    assert "DEXUSEU" in fd.FRED_MAIN and "DGS30" in fd.FRED_MAIN and fd.FRED_FILES["fred_chf.csv"] == "DEXSZUS"


def test_existing_files_are_skipped_without_downloading(tmp_path, monkeypatch, capsys):
    for d, names in (("fred", ["fred_raw.csv", "fred_chf.csv"]), ("bcbs", ["d457_standard.pdf", "d457.txt"])):
        (tmp_path / d).mkdir()
        for n in names:
            (tmp_path / d / n).write_text("x")
    monkeypatch.setattr(fd, "FRED_DIR", tmp_path / "fred")
    monkeypatch.setattr(fd, "BCBS_DIR", tmp_path / "bcbs")
    monkeypatch.setattr(fd, "ROOT", tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not download when files exist")
    monkeypatch.setattr(fd, "download", boom)
    assert fd.main(["--standard"]) == 0
    assert capsys.readouterr().out.count("skip") == 4


def test_atomic_write_leaves_no_partial_file(tmp_path):
    fd.write_atomic(tmp_path / "sub" / "a.csv", b"abc")
    assert (tmp_path / "sub" / "a.csv").read_bytes() == b"abc"
    assert not list((tmp_path / "sub").glob("*.part"))


@pytest.mark.skipif(not (PDF.exists() and TXT.exists()), reason="standard not fetched (python scripts/fetch_data.py --standard)")
def test_extraction_reproduces_the_text_the_parameters_were_built_from(tmp_path):
    out = tmp_path / "d457.txt"
    pages = fd.extract_text(PDF, out)
    assert pages == 136
    assert out.read_text(encoding="utf-8") == TXT.read_text(encoding="utf-8")


@pytest.mark.skipif(not FRED_RAW.exists(), reason="FRED data not fetched (python scripts/fetch_data.py)")
def test_local_fred_file_passes_validation():
    fd.validate_fred_csv(FRED_RAW.read_bytes(), fd.FRED_MAIN)
