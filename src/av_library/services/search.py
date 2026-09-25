"""Search the local catalog without sending user queries to a provider."""

from dataclasses import dataclass

from sqlalchemy import or_, select

from av_library.db.database import Database
from av_library.db.models import Actress, ActressMovie, Movie
from av_library.parsing.codes import CodeParser
from av_library.services.actresses import ActressService
from av_library.services.matching import MatchService


@dataclass(frozen=True)
class SearchHit:
    actress_id: int
    actress_name: str
    movie_id: int
    code: str
    title: str
    status: str
    local_paths: tuple[str, ...]


@dataclass(frozen=True)
class SearchResults:
    actresses: tuple[Actress, ...]
    movies: tuple[SearchHit, ...]


class SearchService:
    def __init__(self, database: Database):
        self.database = database

    def search(self, query: str, limit: int = 100) -> SearchResults:
        query = query.strip()[:100]
        if not query:
            return SearchResults((), ())
        actresses = tuple(ActressService(self.database).list(query))
        # Escape LIKE metacharacters so a literal filename/code search stays literal.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        try:
            canonical = CodeParser().normalize_manual(query)
        except ValueError:
            canonical = None
        with self.database.sessions() as session:
            rows = list(
                session.execute(
                    select(Movie, Actress)
                    .join(ActressMovie, ActressMovie.movie_id == Movie.id)
                    .join(Actress, Actress.id == ActressMovie.actress_id)
                    .where(
                        or_(
                            Movie.code.ilike(pattern, escape="\\"),
                            Movie.code == canonical if canonical else Movie.code == "",
                            Movie.title.ilike(pattern, escape="\\"),
                            Movie.japanese_title.ilike(pattern, escape="\\"),
                        )
                    )
                    .order_by(Movie.release_date.desc().nulls_last(), Movie.code)
                    .limit(limit)
                )
            )
        matcher = MatchService(self.database)
        views_by_actress = {}
        hits = []
        for movie, actress in rows:
            if actress.id not in views_by_actress:
                views_by_actress[actress.id] = {
                    view.movie.id: view for view in matcher.views(actress.id)
                }
            view = views_by_actress[actress.id][movie.id]
            hits.append(
                SearchHit(
                    actress.id,
                    actress.name,
                    movie.id,
                    movie.code,
                    movie.title or movie.japanese_title,
                    view.status,
                    view.local_paths,
                )
            )
        return SearchResults(actresses, tuple(hits))
