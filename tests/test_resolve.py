import csv

from cde.resolve import resolve_titles


def _write_csv(path, rows, header=("title", "year")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _resolve(con, tmp_path, rows, name="input.csv"):
    csv_path = tmp_path / name
    _write_csv(csv_path, rows)
    out_path, matched, total = resolve_titles(con, csv_path, "title", "year")
    return _read_csv(out_path), matched, total


def test_exact_primary_and_year_match(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("The Shawshank Redemption", "1994")])
    assert matched == total == 1
    assert out[0]["tconst"] == "tt0001111"


def test_article_stripped_match(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("Seventh Seal", "1957")])
    assert matched == 1
    assert out[0]["tconst"] == "tt0003333"


def test_original_title_match(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("Otto e mezzo", "1963")])
    assert matched == 1
    assert out[0]["tconst"] == "tt0002222"


def test_year_within_one_matches(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("Dune", "1985")])
    assert matched == 1
    assert out[0]["tconst"] == "tt0004444"


def test_year_off_by_two_does_not_match(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("Dune", "1982")])
    assert matched == 0
    assert out[0]["tconst"] == ""


def test_tie_break_higher_votes_wins(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("Nightfall", "2005")])
    assert matched == 1
    # tt0009999 has 50000 votes vs tt0008888's 5000 -- identity disambiguation,
    # not a quality judgment.
    assert out[0]["tconst"] == "tt0009999"


def test_case_and_punctuation_normalization(con, tmp_path):
    out, matched, total = _resolve(con, tmp_path, [("the SHAWSHANK, Redemption!!!", "1994")])
    assert matched == 1
    assert out[0]["tconst"] == "tt0001111"


def test_unmatched_counted_correctly(con, tmp_path):
    rows = [
        ("The Shawshank Redemption", "1994"),
        ("Seventh Seal", "1957"),
        ("Otto e mezzo", "1963"),
        ("Nonexistent Film Title", "2050"),
    ]
    out, matched, total = _resolve(con, tmp_path, rows)
    assert total == 4
    assert matched == 3
    assert matched / total == 0.75
