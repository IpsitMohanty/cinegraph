from conftest import lookup_reconciles


def test_film_keeps_only_movie_type(con):
    types = {row[0] for row in con.execute("SELECT DISTINCT titleType FROM film").fetchall()}
    assert types == {"movie"}


def test_film_drops_adult_rows(con):
    n = con.execute("SELECT COUNT(*) FROM film WHERE isAdult = '1'").fetchone()[0]
    assert n == 0
    # sanity: the adult fixture row really would have matched titleType/isAdult
    # if the filter weren't applied.
    total = con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    assert total == 7  # 9 basics rows - 1 adult - 1 short


def test_year_and_runtime_cast_to_int(con):
    row = con.execute("""
        SELECT startYear, runtimeMinutes FROM film WHERE tconst = 'tt0001111'
    """).fetchone()
    year, runtime = row
    assert year == 1994
    assert runtime == 142
    assert isinstance(year, int)
    assert isinstance(runtime, int)


def test_ratings_and_crew_joined(con):
    row = con.execute("""
        SELECT imdb_rating, imdb_votes, directors, writers
        FROM film WHERE tconst = 'tt0001111'
    """).fetchone()
    rating, votes, directors, writers = row
    assert rating == 9.3
    assert votes == 2900000
    assert directors == "nm0001"
    assert writers == "nm0002"


def test_title_lookup_nonempty_and_has_aka_key(con):
    n = con.execute("SELECT COUNT(*) FROM title_lookup").fetchone()[0]
    assert n > 0

    aka_row = con.execute("""
        SELECT tconst FROM title_lookup WHERE source = 'aka' AND title = 'Seventh Seal'
    """).fetchone()
    assert aka_row == ("tt0003333",)


def test_title_lookup_reconciles(con):
    assert lookup_reconciles(con)
