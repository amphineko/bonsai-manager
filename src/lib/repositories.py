from __future__ import annotations

import json
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter
from sqlalchemy import ForeignKey, String, Text, create_engine, delete, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)

from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot


class AggregateRepository(Protocol):
    def list_all(self) -> list[Aggregate]: ...

    def get_by_short_name(self, short_name: str) -> Aggregate | None: ...

    def get_by_torrent_hash(self, torrent_hash: str) -> Aggregate | None: ...

    def get_by_bangumi_subject_id(self, subject_id: int) -> list[Aggregate]: ...

    def find(
        self,
        short_name_patterns: list[str] | None = None,
        torrent_hashes: list[str] | None = None,
        bangumi_subject_name_patterns: list[str] | None = None,
        bangumi_subject_cn_name_patterns: list[str] | None = None,
    ) -> list[Aggregate]: ...

    def add(self, aggregate: Aggregate) -> None: ...

    def replace(self, aggregate: Aggregate) -> None: ...

    def replace_all(self, aggregates: list[Aggregate]) -> None: ...

    def remove_by_short_name(self, short_name: str) -> Aggregate | None: ...


class JsonAggregateRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def list_all(self) -> list[Aggregate]:
        if not self.db_path.exists():
            return []
        with self.db_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return TypeAdapter(list[Aggregate]).validate_python(data)

    def save_all(self, aggregates: list[Aggregate]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.db_path.open("w", encoding="utf-8") as f:
            data = [aggregate.model_dump() for aggregate in aggregates]
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_by_short_name(self, short_name: str) -> Aggregate | None:
        return next(
            (
                aggregate
                for aggregate in self.list_all()
                if aggregate.short_name == short_name
            ),
            None,
        )

    def get_by_torrent_hash(self, torrent_hash: str) -> Aggregate | None:
        return next(
            (
                aggregate
                for aggregate in self.list_all()
                if any(torrent.hash == torrent_hash for torrent in aggregate.torrents)
            ),
            None,
        )

    def get_by_bangumi_subject_id(self, subject_id: int) -> list[Aggregate]:
        return [
            aggregate
            for aggregate in self.list_all()
            if any(
                subject.subject_id == subject_id
                for subject in aggregate.bangumi_subjects
            )
        ]

    def find(
        self,
        short_name_patterns: list[str] | None = None,
        torrent_hashes: list[str] | None = None,
        bangumi_subject_name_patterns: list[str] | None = None,
        bangumi_subject_cn_name_patterns: list[str] | None = None,
    ) -> list[Aggregate]:
        return filter_aggregates(
            self.list_all(),
            short_name_patterns,
            torrent_hashes,
            bangumi_subject_name_patterns,
            bangumi_subject_cn_name_patterns,
        )

    def add(self, aggregate: Aggregate) -> None:
        aggregates = self.list_all()
        aggregates.append(aggregate)
        self.save_all(aggregates)

    def replace(self, aggregate: Aggregate) -> None:
        aggregates = []
        replaced = False
        for existing in self.list_all():
            if existing.short_name == aggregate.short_name:
                aggregates.append(aggregate)
                replaced = True
            else:
                aggregates.append(existing)
        if not replaced:
            aggregates.append(aggregate)
        self.save_all(aggregates)

    def replace_all(self, aggregates: list[Aggregate]) -> None:
        self.save_all(aggregates)

    def remove_by_short_name(self, short_name: str) -> Aggregate | None:
        aggregates = self.list_all()
        removed = next(
            (
                aggregate
                for aggregate in aggregates
                if aggregate.short_name == short_name
            ),
            None,
        )
        if removed is None:
            return None
        self.save_all(
            [
                aggregate
                for aggregate in aggregates
                if aggregate.short_name != short_name
            ]
        )
        return removed


class Base(DeclarativeBase):
    pass


class AggregateRow(Base):
    __tablename__ = "aggregates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    short_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String, nullable=False)

    subject_links: Mapped[list[AggregateBangumiSubjectRow]] = relationship(
        back_populates="aggregate",
        cascade="all, delete-orphan",
    )
    torrents: Mapped[list[TorrentRow]] = relationship(
        back_populates="aggregate",
        cascade="all, delete-orphan",
    )


class BangumiSubjectRow(Base):
    __tablename__ = "bangumi_subjects"

    subject_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_cn: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[int | None]
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    aggregate_links: Mapped[list[AggregateBangumiSubjectRow]] = relationship(
        back_populates="subject",
    )


class AggregateBangumiSubjectRow(Base):
    __tablename__ = "aggregate_bangumi_subjects"

    aggregate_id: Mapped[str] = mapped_column(
        ForeignKey("aggregates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("bangumi_subjects.subject_id"),
        primary_key=True,
    )

    aggregate: Mapped[AggregateRow] = relationship(back_populates="subject_links")
    subject: Mapped[BangumiSubjectRow] = relationship(back_populates="aggregate_links")


class TorrentRow(Base):
    __tablename__ = "torrents"

    hash: Mapped[str] = mapped_column(String, primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(
        ForeignKey("aggregates.id", ondelete="CASCADE"),
        nullable=False,
    )

    aggregate: Mapped[AggregateRow] = relationship(back_populates="torrents")


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class SqliteAggregateRepository:
    def __init__(self, db_path: str | Path, create: bool = True):
        self.db_path = Path(db_path)
        if not create and not self.db_path.exists():
            raise FileNotFoundError(f"SQLite database not found: {self.db_path}")
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        if create:
            self.initialize()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def list_all(self) -> list[Aggregate]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(AggregateRow)
                    .options(*aggregate_load_options())
                    .order_by(AggregateRow.short_name)
                )
            )
            return [aggregate_from_row(row) for row in rows]

    def get_by_short_name(self, short_name: str) -> Aggregate | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AggregateRow)
                .where(AggregateRow.short_name == short_name)
                .options(*aggregate_load_options())
            )
            return aggregate_from_row(row) if row else None

    def get_by_torrent_hash(self, torrent_hash: str) -> Aggregate | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AggregateRow)
                .join(TorrentRow)
                .where(TorrentRow.hash == torrent_hash)
                .options(*aggregate_load_options())
            )
            return aggregate_from_row(row) if row else None

    def get_by_bangumi_subject_id(self, subject_id: int) -> list[Aggregate]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(AggregateRow)
                    .join(AggregateBangumiSubjectRow)
                    .where(AggregateBangumiSubjectRow.subject_id == subject_id)
                    .options(*aggregate_load_options())
                    .order_by(AggregateRow.short_name)
                )
            )
            return [aggregate_from_row(row) for row in rows]

    def find(
        self,
        short_name_patterns: list[str] | None = None,
        torrent_hashes: list[str] | None = None,
        bangumi_subject_name_patterns: list[str] | None = None,
        bangumi_subject_cn_name_patterns: list[str] | None = None,
    ) -> list[Aggregate]:
        return filter_aggregates(
            self.list_all(),
            short_name_patterns,
            torrent_hashes,
            bangumi_subject_name_patterns,
            bangumi_subject_cn_name_patterns,
        )

    def add(self, aggregate: Aggregate) -> None:
        with self.session_factory.begin() as session:
            session.add(row_from_aggregate(aggregate, session))

    def replace(self, aggregate: Aggregate) -> None:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(AggregateRow).where(
                    AggregateRow.short_name == aggregate.short_name
                )
            )
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(row_from_aggregate(aggregate, session))

    def replace_all(self, aggregates: list[Aggregate]) -> None:
        with self.session_factory.begin() as session:
            session.execute(delete(TorrentRow))
            session.execute(delete(AggregateBangumiSubjectRow))
            session.execute(delete(AggregateRow))
            session.execute(delete(BangumiSubjectRow))
            for aggregate in aggregates:
                session.add(row_from_aggregate(aggregate, session))

    def remove_by_short_name(self, short_name: str) -> Aggregate | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(AggregateRow)
                .where(AggregateRow.short_name == short_name)
                .options(*aggregate_load_options())
            )
            if row is None:
                return None
            aggregate = aggregate_from_row(row)
            session.delete(row)
            return aggregate


def aggregate_load_options():
    return (
        selectinload(AggregateRow.subject_links).selectinload(
            AggregateBangumiSubjectRow.subject
        ),
        selectinload(AggregateRow.torrents),
    )


def stable_aggregate_id(short_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bonsai-manager:aggregate:{short_name}"))


def aggregate_from_row(row: AggregateRow) -> Aggregate:
    subjects = []
    for link in sorted(row.subject_links, key=lambda item: item.subject_id):
        subject = link.subject
        subjects.append(
            BangumiSubject(
                subject_id=subject.subject_id,
                last_updated_at=subject.updated_at,
                snapshot=BangumiSubjectSnapshot(
                    name=subject.name,
                    name_cn=subject.name_cn,
                    type=subject.type,
                    tags=json.loads(subject.tags_json),
                ),
            )
        )

    return Aggregate(
        short_name=row.short_name,
        category=row.category,
        bangumi_subjects=subjects,
        torrents=[
            Torrent(hash=torrent.hash)
            for torrent in sorted(row.torrents, key=lambda item: item.hash)
        ],
    )


def row_from_aggregate(aggregate: Aggregate, session: Session) -> AggregateRow:
    row = AggregateRow(
        id=stable_aggregate_id(aggregate.short_name),
        short_name=aggregate.short_name,
        category=aggregate.category,
    )
    for subject in aggregate.bangumi_subjects:
        snapshot = subject.snapshot
        subject_row = session.get(BangumiSubjectRow, subject.subject_id)
        if subject_row is None:
            subject_row = BangumiSubjectRow(
                subject_id=subject.subject_id,
                name=snapshot.name if snapshot else "",
                name_cn=snapshot.name_cn if snapshot else "",
                type=snapshot.type if snapshot else None,
                tags_json=json.dumps(
                    [tag.model_dump() for tag in snapshot.tags] if snapshot else [],
                    ensure_ascii=False,
                ),
                updated_at=subject.last_updated_at,
            )
        elif snapshot is not None:
            subject_row.name = snapshot.name
            subject_row.name_cn = snapshot.name_cn
            subject_row.type = snapshot.type
            subject_row.tags_json = json.dumps(
                [tag.model_dump() for tag in snapshot.tags],
                ensure_ascii=False,
            )
            subject_row.updated_at = subject.last_updated_at
        row.subject_links.append(
            AggregateBangumiSubjectRow(
                subject_id=subject.subject_id,
                subject=subject_row,
            )
        )

    row.torrents = [
        TorrentRow(hash=torrent.hash)
        for torrent in sorted(aggregate.torrents, key=lambda item: item.hash)
    ]
    return row


def filter_aggregates(
    aggregates: list[Aggregate],
    short_name_patterns: list[str] | None = None,
    torrent_hashes: list[str] | None = None,
    bangumi_subject_name_patterns: list[str] | None = None,
    bangumi_subject_cn_name_patterns: list[str] | None = None,
) -> list[Aggregate]:
    short_name_patterns = short_name_patterns or []
    torrent_hashes = torrent_hashes or []
    bangumi_subject_name_patterns = bangumi_subject_name_patterns or []
    bangumi_subject_cn_name_patterns = bangumi_subject_cn_name_patterns or []
    filter_torrent_hash_set = set(torrent_hashes)
    matches = []
    for aggregate in aggregates:
        short_name_matches = any(
            fnmatch(aggregate.short_name, pattern) for pattern in short_name_patterns
        )
        torrent_hash_matches = any(
            torrent.hash in filter_torrent_hash_set for torrent in aggregate.torrents
        )
        bangumi_subject_name_matches = any(
            subject.snapshot
            and any(
                fnmatch(subject.snapshot.name, pattern)
                for pattern in bangumi_subject_name_patterns
            )
            for subject in aggregate.bangumi_subjects
        )
        bangumi_subject_cn_name_matches = any(
            subject.snapshot
            and any(
                fnmatch(subject.snapshot.name_cn, pattern)
                for pattern in bangumi_subject_cn_name_patterns
            )
            for subject in aggregate.bangumi_subjects
        )
        if (
            short_name_matches
            or torrent_hash_matches
            or bangumi_subject_name_matches
            or bangumi_subject_cn_name_matches
        ):
            matches.append(aggregate)
    return matches


def create_repository(
    db_backend: str,
    db_path: Path,
) -> AggregateRepository:
    match db_backend:
        case "sqlite":
            return SqliteAggregateRepository(db_path)
        case "json":
            return JsonAggregateRepository(db_path)
        case _:
            raise ValueError(f"Invalid backend {db_backend}")
