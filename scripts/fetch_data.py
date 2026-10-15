"""Download the public inputs this project needs (they are not committed to the repo).

    python scripts/fetch_data.py              # FRED yields and FX  -> refs/fred/   (needed by run_all.py)
    python scripts/fetch_data.py --standard   # also the BCBS d457 PDF + extracted text -> refs/bcbs_d457/
                                              # (only needed to re-run scripts/extract_params.py)
    python scripts/fetch_data.py --force      # overwrite files that already exist

Sources
  FRED (Federal Reserve Bank of St. Louis): daily US Treasury constant-maturity yields (H.15) and
  EUR/USD, GBP/USD, CHF/USD rates (H.10). Check FRED's terms of use before redistributing the files.
  BIS: Basel Committee on Banking Supervision, "Minimum capital requirements for market risk" (d457),
  January 2019. A BIS publication: do not commit the PDF or the text extracted from it.
"""
import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRED_DIR = ROOT / "refs" / "fred"
BCBS_DIR = ROOT / "refs" / "bcbs_d457"

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
FRED_MAIN = "DGS3MO,DGS6MO,DGS1,DGS2,DGS3,DGS5,DGS10,DGS20,DGS30,DEXUSEU,DEXJPUS,DEXUSUK"
FRED_FILES = {"fred_raw.csv": FRED_MAIN, "fred_chf.csv": "DEXSZUS"}
# NB: the 'landing page' URL bis.org/bcbs/publ/d457.pdf returns HTML; this is the actual PDF.
BCBS_URL = "https://www.bis.org/publications/201901-standards-minimum-capital-requirements-market-risk.pdf"
UA = {"User-Agent": "Mozilla/5.0 (frtb-sbm-calculator data fetch)"}


def download(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def validate_fred_csv(data: bytes, first_series: str) -> None:
    head = data[:300].decode("utf-8", errors="replace")
    if not head.startswith("observation_date," + first_series.split(",")[0]):
        raise ValueError(f"unexpected FRED header: {head[:80]!r}")
    if data.count(b"\n") < 1000:
        raise ValueError("FRED file looks too short")


def validate_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF (the BIS site may have returned an HTML page)")


def extract_text(pdf_path: Path, txt_path: Path) -> int:
    """Same extraction used to build config/*.csv: pypdf text, pages joined by a marker line."""
    import pypdf
    reader = pypdf.PdfReader(str(pdf_path))
    txt_path.write_text("\n\n=====PAGE=====\n".join((p.extract_text() or "") for p in reader.pages), encoding="utf-8")
    return len(reader.pages)


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--standard", action="store_true", help="also fetch the BCBS d457 PDF and extract its text")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args(argv)

    for name, ids in FRED_FILES.items():
        dest = FRED_DIR / name
        if dest.exists() and not args.force:
            print(f"skip   {dest.relative_to(ROOT)} (exists)")
            continue
        data = download(FRED_BASE + ids)
        validate_fred_csv(data, ids)
        write_atomic(dest, data)
        print(f"saved  {dest.relative_to(ROOT)}  ({len(data) / 1e3:,.0f} kB)")

    if args.standard:
        pdf, txt = BCBS_DIR / "d457_standard.pdf", BCBS_DIR / "d457.txt"
        if pdf.exists() and not args.force:
            print(f"skip   {pdf.relative_to(ROOT)} (exists)")
        else:
            data = download(BCBS_URL)
            validate_pdf(data)
            write_atomic(pdf, data)
            print(f"saved  {pdf.relative_to(ROOT)}  ({len(data) / 1e6:,.1f} MB)")
        if txt.exists() and not args.force:
            print(f"skip   {txt.relative_to(ROOT)} (exists)")
        else:
            print(f"saved  {txt.relative_to(ROOT)}  ({extract_text(pdf, txt)} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
