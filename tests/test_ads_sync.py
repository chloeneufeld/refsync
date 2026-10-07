"""ADS sync against a mocked ADS API, on a mix of arXiv and ADS-only papers."""

import json
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from refsync.db import SQLiteDatabase, SQLitePaperRepository
from refsync.models import Paper, PaperUpdate
from refsync.services import ads as ads_mod
from refsync.services.identifiers import make_paper_id

# Fake ADS index
DOCS = [
    {  # arXiv paper that has since been published
        "bibcode": "2023ApJ...950...12P",
        "identifier": ["arXiv:2301.07041", "2023ApJ...950...12P", "10.3847/abc"],
        "doi": ["10.3847/abc"],
        "pub": "The Astrophysical Journal",
        "volume": "950",
        "page": ["12"],
        "doctype": "article",
    },
    {  # old-style arXiv id with a 'v' in the archive name, still a preprint
        "bibcode": "1999solv.int..1001X",
        "identifier": ["arXiv:solv-int/9901001"],
        "pub": "arXiv e-prints",
        "doctype": "eprint",
    },
    {  # ADS-only paper stored under its arXiv-style bibcode, now re-keyed to a journal
        "bibcode": "2025MNRAS.530..100K",
        "alternate_bibcode": ["2024arXiv240712345K"],
        "identifier": ["2024arXiv240712345K", "2025MNRAS.530..100K"],
        "doi": ["10.1093/mnras/xyz"],
        "pub": "Monthly Notices of the Royal Astronomical Society",
        "volume": "530",
        "page": ["100"],
        "doctype": "article",
    },
]


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/search/query"):
        q = parse_qs(urlparse(str(request.url)).query)["q"][0]
        wanted = [s.strip('"') for s in q[q.index("(") + 1 : -1].split(" OR ")]
        hits = []
        for doc in DOCS:
            names = {doc["bibcode"], *doc.get("identifier", []), *doc.get("alternate_bibcode", [])}
            if names & set(wanted):
                hits.append(doc)
        return httpx.Response(200, json={"response": {"docs": hits}})
    if request.url.path.endswith("/export/bibtex"):
        codes = json.loads(request.content)["bibcode"]
        export = "\n\n".join(f"@ARTICLE{{{b},\n  title = {{X}}\n}}" for b in codes)
        return httpx.Response(200, json={"export": export})
    return httpx.Response(404)


@pytest.fixture
def mock_ads(monkeypatch):
    transport = httpx.MockTransport(_handler)
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ads_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(ads_mod, "get_ads_api_key", lambda: "x" * 20)


@pytest.fixture
async def repo(tmp_path):
    db = SQLiteDatabase(tmp_path / "library.db")
    await db.connect()
    yield SQLitePaperRepository(db)
    await db.disconnect()


def _arxiv(aid, key):
    return Paper(
        id=make_paper_id(arxiv_id=aid), arxiv_id=aid, title=aid, authors=["A B"],
        published=datetime(2023, 1, 1), cite_key=key,
    )


def _ads(bibcode, key):
    return Paper(
        id=make_paper_id(bibcode=bibcode), bibcode=bibcode, source="ads", title=bibcode,
        authors=["A B"], published=datetime(2024, 7, 1), cite_key=key,
    )


async def test_sync_mixed_library(mock_ads, repo):
    papers = [
        _arxiv("2301.07041", "B:2023"),
        _arxiv("solv-int/9901001", "X:1999"),
        _arxiv("2401.99999", "Gone:2024"),  # not in ADS
        _ads("2024arXiv240712345K", "K:2024"),
        _ads("2020Nope..1..1Z", "Z:2020"),  # ADS-only, not found
    ]
    for p in papers:
        await repo.create(p)

    async def update(id, updates):
        assert await repo.get(id) is not None, f"callback got unknown id {id}"
        await repo.update(id, PaperUpdate(**updates))

    stats = await ads_mod.sync_papers_with_ads(papers, update)
    assert stats == {
        "synced": 3, "published": 2, "unchanged": 0, "not_found": 2, "skipped": 0, "errors": 0,
    }

    pub = await repo.get(make_paper_id(arxiv_id="2301.07041"))
    assert pub.is_published and pub.bibcode == "2023ApJ...950...12P"
    assert pub.doi == "10.3847/abc"
    assert pub.journal_ref == "The Astrophysical Journal, 950, 12"
    assert pub.bibtex.startswith("@ARTICLE{B:2023,") and pub.bibtex_source == "ads"

    old_style = await repo.get(make_paper_id(arxiv_id="solv-int/9901001"))
    assert not old_style.is_published and old_style.bibcode == "1999solv.int..1001X"

    ads_only = await repo.get(make_paper_id(bibcode="2024arXiv240712345K"))
    assert ads_only.is_published and ads_only.bibcode == "2025MNRAS.530..100K"
    assert ads_only.ads_url.endswith("/abs/2025MNRAS.530..100K/abstract")
    assert ads_only.bibtex.startswith("@ARTICLE{K:2024,")

    for missing in (make_paper_id(arxiv_id="2401.99999"), make_paper_id(bibcode="2020Nope..1..1Z")):
        p = await repo.get(missing)
        assert p.last_citation_sync is not None and not p.is_published


async def test_search_by_arxiv_ids_skips_none(mock_ads):
    client = ads_mod.ADSClient()
    assert await client.search_by_arxiv_ids([None, None]) == {}
    res = await client.search_by_arxiv_ids([None, "2301.07041v2"])
    assert res["2301.07041v2"]["bibcode"] == "2023ApJ...950...12P"


async def test_add_paper_dedupes_cite_key(repo, monkeypatch):
    from refsync.routers import papers as papers_router

    first = _arxiv("2301.00001", "Smith:2023")
    first.authors = ["John Smith"]
    await repo.create(first)

    second = _arxiv("2301.00002", "Smith:2023")
    second.authors = ["Jane Smith"]
    second.bibtex = "@ARTICLE{Smith:2023,\n  title = {Y}\n}"

    async def fake_resolve(_):
        return second

    monkeypatch.setattr(papers_router, "resolve_paper", fake_resolve)
    created = await papers_router.add_paper(papers_router.PaperCreate(arxiv_url="x"), repo)
    assert created.cite_key == "Smith:2023a"
    assert created.bibtex.startswith("@ARTICLE{Smith:2023a,")
