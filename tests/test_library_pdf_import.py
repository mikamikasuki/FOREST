"""Public PDF imports must keep source bytes and passages together."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject, DictionaryObject, NameObject,
)

from research.literature import sources


def pdf_bytes(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("concurrent", [False, True])
def test_public_pdfs_with_same_basename_preserve_each_source(tmp_path, monkeypatch, concurrent):
    urls = ["https://papers.example/alpha/source.pdf", "https://papers.example/beta/source.pdf"]
    texts = ["First source evidence", "Second source evidence"]
    payloads = dict(zip(urls, map(pdf_bytes, texts)))
    barrier = Barrier(2) if concurrent else None

    def download(url, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[url])
        if barrier:
            barrier.wait(timeout=5)
        return str(target)

    monkeypatch.setattr(sources, "download_pdf", download)
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(lambda url: sources.import_identifier(url, tmp_path), urls))
    else:
        first = sources.import_identifier(urls[0], tmp_path)
        first_path, first_passages = first["pdf_path"], first["passages"].copy()
        records = [first, sources.import_identifier(urls[1], tmp_path)]
        assert first["pdf_path"] == first_path
        assert first["passages"] == first_passages

    assert records[0]["pdf_path"] != records[1]["pdf_path"]
    for record, url, text in zip(records, urls, texts):
        path = Path(record["pdf_path"])
        assert path.is_relative_to(tmp_path)
        assert path.read_bytes() == payloads[url]
        assert record["url"] == url
        assert record["read_scope"] == "full_text"
        assert [p["text"] for p in record["passages"]] == [text]
        assert record["passages"] == sources.extract_pdf(path)
        assert all(p["source_file"] == path.name for p in record["passages"])


def test_reimported_url_cannot_mutate_existing_pdf(tmp_path, monkeypatch):
    payloads = iter([pdf_bytes("Original evidence"), pdf_bytes("Updated source")])

    def download(url, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(next(payloads))
        return str(target)

    monkeypatch.setattr(sources, "download_pdf", download)
    first = sources.import_identifier("https://papers.example/source.pdf", tmp_path)
    original = Path(first["pdf_path"]).read_bytes()
    second = sources.import_identifier("https://papers.example/source.pdf", tmp_path)
    assert Path(first["pdf_path"]).read_bytes() == original
    assert first["pdf_path"] != second["pdf_path"]
    assert sources.extract_pdf(first["pdf_path"]) == first["passages"]


def test_non_public_pdf_imports_keep_existing_behavior(tmp_path, monkeypatch):
    local = tmp_path / "local.pdf"
    local.write_bytes(pdf_bytes("Local source"))
    local_record = sources.import_identifier(str(local), tmp_path)
    assert local_record["pdf_path"] == str(local)
    assert local_record["source"] == "local_pdf"

    arxiv = {"title": "arXiv source", "authors": [], "url": "https://arxiv.org/abs/2401.12345"}
    monkeypatch.setattr(sources, "_arxiv", lambda params: [arxiv.copy()])
    assert sources.import_identifier("2401.12345", tmp_path)["title"] == arxiv["title"]

    class Response:
        def json(self):
            return {"message": {"title": ["DOI source"], "DOI": "10.1234/control"}}

    monkeypatch.setattr(sources, "_get", lambda *args, **kwargs: Response())
    doi = sources.import_identifier("10.1234/control", tmp_path)
    assert doi["doi"] == "10.1234/control"
    assert doi["source"] == "crossref"
    assert "pdf_path" not in doi
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "references.bib").exists()


@pytest.mark.parametrize("suffix", ["?utm_source=forest-check", "#citation"])
def test_doi_resolver_urls_ignore_query_and_fragment(tmp_path, monkeypatch, suffix):
    requested = []

    class Response:
        def json(self):
            return {"message": {"title": ["DOI source"], "DOI": "10.1234/control"}}

    def get(url, *args, **kwargs):
        requested.append(url)
        return Response()

    monkeypatch.setattr(sources, "_get", get)
    record = sources.import_identifier(f"https://doi.org/10.1234/control{suffix}", tmp_path)

    assert record["doi"] == "10.1234/control"
    assert requested == ["https://api.crossref.org/works/10.1234%2Fcontrol"]
