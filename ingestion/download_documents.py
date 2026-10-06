"""Download the 5 PDFs and the WHO HTML page, recording retrieval_date.

Fails loudly: every document is attempted, then the script exits non-zero listing every
failure (HTTP error, wrong content type, not a PDF, suspiciously small file).

    python -m ingestion.download_documents [--only ID ...] [--force]
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import date

import httpx

from ingestion.common import DATA_DIR, MANIFEST_PATH, RAW_DIR, Document, load_manifest, load_registry

logger = logging.getLogger("ingestion.download")

# fda.gov's bot protection serves an "abuse detection" 404 to browser-like User-Agents,
# while dietaryguidelines.gov blocks everything; a plain, honest UA works where allowed.
USER_AGENT = "nutrition-assistant-ingestion/1.0 (+research; one-off corpus download)"
MIN_BYTES = 5_000


class DownloadError(RuntimeError):
    pass


def fetch(doc: Document, client: httpx.Client) -> bytes:
    try:
        response = client.get(doc.fetch_url)
    except httpx.HTTPError as exc:
        raise DownloadError(f"request to {doc.fetch_url} failed: {exc!r}") from exc
    if response.status_code != 200:
        raise DownloadError(f"HTTP {response.status_code} from {response.url}")
    body = response.content
    if len(body) < MIN_BYTES:
        raise DownloadError(f"only {len(body)} bytes from {response.url}")
    content_type = response.headers.get("content-type", "")
    if doc.format == "pdf":
        if not body.startswith(b"%PDF-"):
            raise DownloadError(f"{response.url} is not a PDF (content-type {content_type!r})")
    elif "html" not in content_type.lower():
        raise DownloadError(f"{response.url} is not HTML (content-type {content_type!r})")
    return body


def download(docs: list[Document], force: bool = False) -> dict[str, str]:
    """Returns {doc id: error message} for failures; successes are written to the manifest."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    failures: dict[str, str] = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=180
    ) as client:
        for doc in docs:
            if doc.raw_path.exists() and doc.id in manifest and not force:
                logger.info("already downloaded: %s", doc.id)
                continue
            try:
                body = fetch(doc, client)
            except DownloadError as exc:
                logger.error("FAILED %s: %s", doc.id, exc)
                failures[doc.id] = str(exc)
                continue
            doc.raw_path.write_bytes(body)
            manifest[doc.id] = {
                "retrieval_date": date.today().isoformat(),
                "fetched_from": doc.fetch_url,
                "source_url": doc.source_url,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "file": doc.raw_path.name,
            }
            logger.info("downloaded %s (%d bytes)", doc.id, len(body))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="document ids (default: all)")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    failures = download(load_registry(args.only), force=args.force)
    if failures:
        print("\nDownload failures:", file=sys.stderr)
        for doc_id, message in failures.items():
            print(f"  {doc_id}: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
