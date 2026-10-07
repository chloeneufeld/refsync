from datetime import datetime

import pytest

from refsync.models import Paper
from refsync.services.bibtex import (
    _split_name,
    format_authors_bibtex,
    generate_cite_key,
    surname_slug,
    update_cite_key_in_bibtex,
)
from refsync.services.pdf import generate_pdf_filename


def _paper(author: str, year: int = 2026, **kw) -> Paper:
    return Paper(
        id=kw.pop("id", "arxiv-2601.01234"),
        arxiv_id=kw.pop("arxiv_id", "2601.01234"),
        title="T",
        authors=[author],
        published=datetime(year, 3, 1),
        **kw,
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Pieter van Dokkum", ("Pieter", "van Dokkum", "")),
        ("van Dokkum, Pieter G.", ("Pieter G.", "van Dokkum", "")),
        ("Arjen van der Wel", ("Arjen", "van der Wel", "")),
        ("van der Wel, Arjen", ("Arjen", "van der Wel", "")),
        ("Juan de la Cruz", ("Juan", "de la Cruz", "")),
        ("Mark den Brok", ("Mark", "den Brok", "")),
        ("J. Xavier Prochaska", ("J. Xavier", "Prochaska", "")),
        ("Claude-André Faucher-Giguère", ("Claude-André", "Faucher-Giguère", "")),
        ("John Smith Jr.", ("John", "Smith", "Jr.")),
        ("John Smith, Jr.", ("John", "Smith", "Jr.")),
        ("Smith, Jr., John", ("John", "Smith", "Jr.")),
        ("Smith, John, Jr.", ("John", "Smith", "Jr.")),
        ("Robert Kennicutt III", ("Robert", "Kennicutt", "III")),
        ("Kere{\\v{s}}, Du{\\v{s}}an", ("Dušan", "Kereš", "")),
        ("Du{\\v{s}}an Kere{\\v{s}}", ("Dušan", "Kereš", "")),
        ("Plato", ("", "Plato", "")),
        ("", ("", "", "")),
        ("Euclid Collaboration", ("", "Euclid Collaboration", "")),
        ("Euclid Collaboration: Y. Mellier", ("", "Euclid Collaboration", "")),
        ("Collaboration, Euclid", ("", "Euclid Collaboration", "")),
        ("The LIGO Scientific Collaboration", ("", "LIGO Scientific Collaboration", "")),
        ("HSC Team", ("", "HSC Team", "")),
        ("SKA Consortium", ("", "SKA Consortium", "")),
    ],
)
def test_split_name(name, expected):
    assert _split_name(name) == expected


@pytest.mark.parametrize(
    "author, key",
    [
        ("Pieter van Dokkum", "van_Dokkum:2026"),
        ("van Dokkum, Pieter", "van_Dokkum:2026"),
        ("Arjen van der Wel", "van_der_Wel:2026"),
        ("Claude-André Faucher-Giguère", "Faucher-Giguere:2026"),
        ("Dušan Kereš", "Keres:2026"),
        ("Kere{\\v{s}}, Du{\\v{s}}an", "Keres:2026"),
        ("Bjørn Ølsen", "Olsen:2026"),
        ("Łukasz Wyrzykowski", "Wyrzykowski:2026"),
        ("Hans Großmann", "Grossmann:2026"),
        ("Brian O'Shea", "OShea:2026"),
        ("John Smith Jr.", "Smith:2026"),
        ("Imad Pasha", "Pasha:2026"),
        ("Euclid Collaboration", "Euclid_Collaboration:2026"),
        ("Euclid Collaboration: Y. Mellier", "Euclid_Collaboration:2026"),
        ("DESI Collaboration", "DESI_Collaboration:2026"),
        ("Fermi-LAT Collaboration", "Fermi-LAT_Collaboration:2026"),
    ],
)
def test_cite_key(author, key):
    k = generate_cite_key(_paper(author))
    assert k == key
    assert k.isascii() and " " not in k


def test_cite_key_collisions():
    p = _paper("Pieter van Dokkum")
    assert generate_cite_key(p, {"van_Dokkum:2026"}) == "van_Dokkum:2026a"
    taken = {"van_Dokkum:2026"} | {f"van_Dokkum:2026{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
    assert generate_cite_key(p, taken) == "van_Dokkum:2026_2601_01234"


def test_cite_key_ads_only_fallback_is_ascii():
    p = _paper("A Smith", id="ads-abc", arxiv_id=None, bibcode="2020A&A...641A...6P")
    taken = {"Smith:2026"} | {f"Smith:2026{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
    key = generate_cite_key(p, taken)
    assert key == "Smith:2026_2020A_A___641A___6P"


def test_format_authors_bibtex():
    out = format_authors_bibtex(
        [
            "Pieter van Dokkum",
            "Kereš, Dušan",
            "John Smith Jr.",
            "Imad Pasha",
            "Plato",
            "Euclid Collaboration",
        ]
    )
    assert out == (
        "{van Dokkum}, Pieter and {Kereš}, Dušan and {Smith}, Jr., John "
        "and {Pasha}, Imad and {Plato} and {Euclid Collaboration}"
    )


def test_surname_slug_and_pdf_filename():
    assert surname_slug("Pieter van Dokkum") == "van_Dokkum"
    assert generate_pdf_filename(_paper("Pieter van Dokkum")) == "van_Dokkum_2026_2601.01234.pdf"


def test_update_cite_key_in_bibtex():
    bib = "@ARTICLE{2023ApJ...1..1B,\n  author = {x}\n}"
    assert update_cite_key_in_bibtex(bib, "van_Dokkum:2026").startswith("@ARTICLE{van_Dokkum:2026,")
