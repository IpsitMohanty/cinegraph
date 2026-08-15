from cde.people import compute_density


def test_category_filter_keeps_creative_drops_self_and_archive(con_people):
    categories = {
        row[0] for row in con_people.execute(
            "SELECT DISTINCT category FROM credits"
        ).fetchall()
    }
    assert categories == {
        "director", "writer", "cinematographer", "actor", "composer", "producer",
    }
    assert "self" not in categories
    assert "archive_footage" not in categories


def test_credits_restricted_to_film_tconsts(con_people):
    # tt0007777 is a short -- excluded from `film` by the default --types
    # filter -- so its principals row must be dropped by the JOIN.
    n = con_people.execute(
        "SELECT COUNT(*) FROM credits WHERE tconst = 'tt0007777'"
    ).fetchone()[0]
    assert n == 0

    tconsts = {r[0] for r in con_people.execute("SELECT DISTINCT tconst FROM credits").fetchall()}
    assert tconsts == {"tt0001111", "tt0002222", "tt0003333"}


def test_credits_row_count(con_people):
    n = con_people.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
    assert n == 8


def test_person_restricted_to_credited_nconsts(con_people):
    persons = {r[0] for r in con_people.execute("SELECT nconst FROM person").fetchall()}
    assert persons == {"nm1001", "nm1002", "nm1003", "nm1004", "nm1005", "nm1008"}

    # nm1006 only appears via a dropped archive_footage row, nm1007 only via
    # a dropped non-film row, nm9999 is never credited at all -- none should
    # reach `person`.
    assert "nm1006" not in persons
    assert "nm1007" not in persons
    assert "nm9999" not in persons


def test_person_primary_name_carried_through(con_people):
    row = con_people.execute(
        "SELECT primary_name FROM person WHERE nconst = 'nm1004'"
    ).fetchone()
    assert row == ("Pat Player",)


def test_density_coverage_correct_on_fixture(con_people):
    stats = compute_density(con_people)

    assert stats["total_films"] == 7
    assert stats["total_edges"] == 8
    assert stats["total_persons"] == 6

    assert stats["coverage"]["director"] == {"films": 2, "pct": round(2 / 7 * 100, 1)}
    assert stats["coverage"]["writer"] == {"films": 1, "pct": round(1 / 7 * 100, 1)}
    assert stats["coverage"]["cinematographer"] == {"films": 2, "pct": round(2 / 7 * 100, 1)}
    assert stats["coverage"]["actor"] == {"films": 1, "pct": round(1 / 7 * 100, 1)}
    assert stats["coverage"]["composer"] == {"films": 1, "pct": round(1 / 7 * 100, 1)}
    assert stats["coverage"]["producer"] == {"films": 1, "pct": round(1 / 7 * 100, 1)}
    assert "actress" not in stats["coverage"]
    assert "editor" not in stats["coverage"]


def test_density_collaborators_per_film_correct_on_fixture(con_people):
    stats = compute_density(con_people)

    # tt0001111: 4 collaborators, tt0002222: 2, tt0003333: 2.
    assert stats["films_with_credits"] == 3
    assert stats["collab_mean"] == (4 + 2 + 2) / 3
    assert stats["collab_median"] == 2
    assert stats["collab_min"] == 2
    assert stats["collab_max"] == 4


def test_density_person_degree_correct_on_fixture(con_people):
    stats = compute_density(con_people)

    # nm1001:1, nm1002:1, nm1003:2, nm1004:2, nm1005:1, nm1008:1
    assert stats["persons_with_credits"] == 6
    assert stats["degree_mean"] == (1 + 1 + 2 + 2 + 1 + 1) / 6
    assert stats["degree_median"] == 1
    assert stats["degree_max"] == 2
    assert stats["persons_ge5"] == 0
