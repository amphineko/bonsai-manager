from __future__ import annotations

import json
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    create_engine,
    delete,
    event,
    func,
    inspect,
    or_,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)

from lib.models.aggregates import (
    UNGROUPED_TORRENT_GROUP,
    Aggregate,
    Torrent,
    ordered_torrent_groups,
)
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.sql.elements import ColumnElement


class AggregateRepository(Protocol):
    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[AggregateRepository]: ...

    def list_all(self) -> list[Aggregate]: ...

    def count_aggregates(self) -> int: ...

    def get_by_short_name(self, short_name: str) -> Aggregate | None: ...

    def get_by_short_names(self, short_names: list[str]) -> list[Aggregate]: ...

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

    def update_torrents(
        self,
        short_name: str,
        torrents: dict[str, list[Torrent]],
    ) -> None: ...

    def update_bangumi_subjects(
        self,
        short_name: str,
        subjects: list[BangumiSubject],
    ) -> None: ...

    def import_all(self, aggregates: list[Aggregate]) -> None: ...

    def remove_by_short_name(self, short_name: str) -> Aggregate | None: ...


class Base(DeclarativeBase):
    pass


class AggregateRow(Base):
    __tablename__ = "aggregates"

    id: Mapped[int] = mapped_column(primary_key=True)
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

    aggregate_id: Mapped[int] = mapped_column(
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
    aggregate_id: Mapped[int] = mapped_column(
        ForeignKey("aggregates.id", ondelete="CASCADE"),
        nullable=False,
    )

    aggregate: Mapped[AggregateRow] = relationship(back_populates="torrents")
    group: Mapped[TorrentGroupRow | None] = relationship(
        back_populates="torrent",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )


class TorrentGroupRow(Base):
    __tablename__ = "torrent_groups"
    __table_args__ = (
        CheckConstraint(
            "length(trim(group_name)) > 0 AND lower(group_name) <> 'ungrouped'",
            name="valid_torrent_group_name",
        ),
    )

    torrent_hash: Mapped[str] = mapped_column(
        ForeignKey("torrents.hash", ondelete="CASCADE"),
        primary_key=True,
    )
    group_name: Mapped[str] = mapped_column(String, nullable=False)

    torrent: Mapped[TorrentRow] = relationship(back_populates="group")


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class SqliteAggregateRepository:
    def __init__(
        self,
        db_path: str | Path,
        create: bool = True,
        session: Session | None = None,
    ):
        self.db_path = Path(db_path)
        self.session = session
        self.engine: Engine | None = None
        self.session_factory: sessionmaker[Session] | None = None
        if session is not None:
            return

        if not create and not self.db_path.exists():
            raise FileNotFoundError(f"SQLite database not found: {self.db_path}")
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        if create:
            self.initialize()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def initialize(self) -> None:
        if self.engine is None:
            raise RuntimeError("Cannot initialize a session-bound repository.")
        Base.metadata.create_all(self.engine)

    def schema_is_ready(self) -> bool:
        if self.engine is None:
            raise RuntimeError("Cannot inspect a session-bound repository.")
        try:
            inspector = inspect(self.engine)
            table_names = set(inspector.get_table_names())
        except SQLAlchemyError:
            return False
        for table_name, table in Base.metadata.tables.items():
            if table_name not in table_names:
                return False
            try:
                column_names = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
            except SQLAlchemyError:
                return False
            if not set(table.columns.keys()).issubset(column_names):
                return False
        aggregate_id_column = next(
            column
            for column in inspector.get_columns(AggregateRow.__tablename__)
            if column["name"] == "id"
        )
        return str(aggregate_id_column["type"]).upper() == "INTEGER"

    @contextmanager
    def get_repository(self, *, write: bool) -> Generator[SqliteAggregateRepository]:
        if self.session_factory is None:
            raise RuntimeError(
                "Cannot open a repository from a session-bound repository."
            )

        with self.session_factory() as session:
            if write:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield SqliteAggregateRepository(
                    self.db_path,
                    create=False,
                    session=session,
                )
            except Exception:
                if write:
                    session.rollback()
                raise
            else:
                if write:
                    session.commit()

    def require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("Repository method requires an active session.")
        return self.session

    def list_all(self) -> list[Aggregate]:
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.list_all()

        session = self.require_session()
        rows = list(
            session.scalars(
                select(AggregateRow)
                .options(*aggregate_load_options())
                .order_by(AggregateRow.short_name)
            )
        )
        return [aggregate_from_row(row) for row in rows]

    def count_aggregates(self) -> int:
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.count_aggregates()

        session = self.require_session()
        return session.scalar(select(func.count()).select_from(AggregateRow)) or 0

    def get_by_short_name(self, short_name: str) -> Aggregate | None:
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.get_by_short_name(short_name)

        session = self.require_session()
        row = session.scalar(
            select(AggregateRow)
            .where(AggregateRow.short_name == short_name)
            .options(*aggregate_load_options())
        )
        return aggregate_from_row(row) if row else None

    def get_by_short_names(self, short_names: list[str]) -> list[Aggregate]:
        if not short_names:
            return []
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.get_by_short_names(short_names)

        session = self.require_session()
        rows = list(
            session.scalars(
                select(AggregateRow)
                .where(AggregateRow.short_name.in_(short_names))
                .options(*aggregate_load_options())
            )
        )
        aggregate_by_short_name = {
            row.short_name: aggregate_from_row(row) for row in rows
        }
        return [
            aggregate_by_short_name[short_name]
            for short_name in short_names
            if short_name in aggregate_by_short_name
        ]

    def get_by_torrent_hash(self, torrent_hash: str) -> Aggregate | None:
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.get_by_torrent_hash(torrent_hash)

        session = self.require_session()
        row = session.scalar(
            select(AggregateRow)
            .join(TorrentRow)
            .where(TorrentRow.hash == torrent_hash)
            .options(*aggregate_load_options())
        )
        return aggregate_from_row(row) if row else None

    def get_by_bangumi_subject_id(self, subject_id: int) -> list[Aggregate]:
        if self.session is None:
            with self.get_repository(write=False) as repo:
                return repo.get_by_bangumi_subject_id(subject_id)

        session = self.require_session()
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
        short_name_patterns = short_name_patterns or []
        torrent_hashes = torrent_hashes or []
        bangumi_subject_name_patterns = bangumi_subject_name_patterns or []
        bangumi_subject_cn_name_patterns = bangumi_subject_cn_name_patterns or []
        conditions: list[ColumnElement[bool]] = []

        conditions.extend(
            AggregateRow.short_name.op("GLOB")(pattern)
            for pattern in short_name_patterns
        )
        if torrent_hashes:
            conditions.append(
                AggregateRow.torrents.any(TorrentRow.hash.in_(torrent_hashes))
            )
        conditions.extend(
            AggregateRow.subject_links.any(
                AggregateBangumiSubjectRow.subject.has(
                    BangumiSubjectRow.name.op("GLOB")(pattern)
                )
            )
            for pattern in bangumi_subject_name_patterns
        )
        conditions.extend(
            AggregateRow.subject_links.any(
                AggregateBangumiSubjectRow.subject.has(
                    BangumiSubjectRow.name_cn.op("GLOB")(pattern)
                )
            )
            for pattern in bangumi_subject_cn_name_patterns
        )

        if not conditions:
            return self.list_all()

        session = self.require_session()
        rows = list(
            session.scalars(
                select(AggregateRow)
                .where(or_(*conditions))
                .options(*aggregate_load_options())
                .order_by(AggregateRow.short_name)
            )
        )
        return [aggregate_from_row(row) for row in rows]

    def add(self, aggregate: Aggregate) -> None:
        if self.session is None:
            with self.get_repository(write=True) as repo:
                repo.add(aggregate)
            return

        session = self.require_session()
        session.add(row_from_aggregate(aggregate, session))

    def update_torrents(
        self,
        short_name: str,
        torrents: dict[str, list[Torrent]],
    ) -> None:
        if self.session is None:
            with self.get_repository(write=True) as repo:
                repo.update_torrents(short_name, torrents)
            return

        session = self.require_session()
        aggregate_id = aggregate_id_for_short_name(session, short_name)
        session.execute(
            delete(TorrentRow).where(TorrentRow.aggregate_id == aggregate_id)
        )
        session.add_all(torrent_rows(torrents, aggregate_id=aggregate_id))

    def update_bangumi_subjects(
        self,
        short_name: str,
        subjects: list[BangumiSubject],
    ) -> None:
        if self.session is None:
            with self.get_repository(write=True) as repo:
                repo.update_bangumi_subjects(short_name, subjects)
            return

        session = self.require_session()
        aggregate_id = aggregate_id_for_short_name(session, short_name)
        session.execute(
            delete(AggregateBangumiSubjectRow).where(
                AggregateBangumiSubjectRow.aggregate_id == aggregate_id
            )
        )
        for subject in subjects:
            upsert_subject_row(session, subject)
            session.add(
                AggregateBangumiSubjectRow(
                    aggregate_id=aggregate_id,
                    subject_id=subject.subject_id,
                )
            )

    def import_all(self, aggregates: list[Aggregate]) -> None:
        if self.session is None:
            with self.get_repository(write=True) as repo:
                repo.import_all(aggregates)
            return

        session = self.require_session()
        session.execute(delete(TorrentGroupRow))
        session.execute(delete(TorrentRow))
        session.execute(delete(AggregateBangumiSubjectRow))
        session.execute(delete(AggregateRow))
        session.execute(delete(BangumiSubjectRow))
        for aggregate in aggregates:
            session.add(row_from_aggregate(aggregate, session))

    def remove_by_short_name(self, short_name: str) -> Aggregate | None:
        if self.session is None:
            with self.get_repository(write=True) as repo:
                return repo.remove_by_short_name(short_name)

        session = self.require_session()
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
        selectinload(AggregateRow.torrents).selectinload(TorrentRow.group),
    )


def aggregate_id_for_short_name(session: Session, short_name: str) -> int:
    aggregate_id = session.scalar(
        select(AggregateRow.id).where(AggregateRow.short_name == short_name)
    )
    if aggregate_id is None:
        raise ValueError(f"Aggregate '{short_name}' not found.")
    return aggregate_id


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

    torrents: dict[str, list[Torrent]] = {}
    for torrent in sorted(row.torrents, key=lambda item: item.hash):
        group_name = (
            torrent.group.group_name
            if torrent.group is not None
            else UNGROUPED_TORRENT_GROUP
        )
        torrents.setdefault(group_name, []).append(Torrent(hash=torrent.hash))

    return Aggregate(
        short_name=row.short_name,
        category=row.category,
        bangumi_subjects=subjects,
        torrents=ordered_torrent_groups(torrents),
    )


def row_from_aggregate(aggregate: Aggregate, session: Session) -> AggregateRow:
    row = AggregateRow(
        short_name=aggregate.short_name,
        category=aggregate.category,
    )
    for subject in aggregate.bangumi_subjects:
        subject_row = upsert_subject_row(session, subject)
        row.subject_links.append(
            AggregateBangumiSubjectRow(
                subject_id=subject.subject_id,
                subject=subject_row,
            )
        )

    row.torrents = torrent_rows(aggregate.torrents)
    return row


def torrent_rows(
    torrents: dict[str, list[Torrent]],
    aggregate_id: int | None = None,
) -> list[TorrentRow]:
    rows = []
    for group_name, grouped_torrents in ordered_torrent_groups(torrents).items():
        for torrent in grouped_torrents:
            row = TorrentRow(hash=torrent.hash, aggregate_id=aggregate_id)
            if group_name != UNGROUPED_TORRENT_GROUP:
                row.group = TorrentGroupRow(group_name=group_name)
            rows.append(row)
    return rows


def upsert_subject_row(session: Session, subject: BangumiSubject) -> BangumiSubjectRow:
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
        session.add(subject_row)
    elif snapshot is not None:
        subject_row.name = snapshot.name
        subject_row.name_cn = snapshot.name_cn
        subject_row.type = snapshot.type
        subject_row.tags_json = json.dumps(
            [tag.model_dump() for tag in snapshot.tags],
            ensure_ascii=False,
        )
        subject_row.updated_at = subject.last_updated_at
    return subject_row
