from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from lib.models.bangumi import BangumiSubject

if TYPE_CHECKING:
    from collections.abc import Iterator

UNGROUPED_TORRENT_GROUP = "ungrouped"


class Torrent(BaseModel):
    hash: str


def ordered_torrent_groups(
    torrents: dict[str, list[Torrent]],
) -> dict[str, list[Torrent]]:
    group_names = sorted(
        group_name
        for group_name, grouped_torrents in torrents.items()
        if group_name != UNGROUPED_TORRENT_GROUP and grouped_torrents
    )
    if torrents.get(UNGROUPED_TORRENT_GROUP):
        group_names.insert(0, UNGROUPED_TORRENT_GROUP)
    return {
        group_name: sorted(torrents[group_name], key=lambda torrent: torrent.hash)
        for group_name in group_names
    }


class Aggregate(BaseModel):
    short_name: str
    category: str = "anime"
    bangumi_subjects: list[BangumiSubject] = Field(default_factory=list)
    torrents: dict[str, list[Torrent]] = Field(default_factory=dict)

    @field_validator("torrents", mode="before")
    @classmethod
    def deserialize_legacy_torrents(
        cls,
        value: object,
    ) -> object:
        if isinstance(value, list):
            return (
                {UNGROUPED_TORRENT_GROUP: value} if value else dict[str, list[object]]()
            )
        return value

    @field_validator("torrents")
    @classmethod
    def normalize_torrent_groups(
        cls,
        value: dict[str, list[Torrent]],
    ) -> dict[str, list[Torrent]]:
        normalized: dict[str, list[Torrent]] = {}
        torrent_groups: dict[str, str] = {}
        for supplied_group_name, torrents in value.items():
            group_name = supplied_group_name.strip()
            if not group_name:
                raise ValueError("Torrent group name cannot be empty.")
            if (
                supplied_group_name != UNGROUPED_TORRENT_GROUP
                and group_name.casefold() == UNGROUPED_TORRENT_GROUP.casefold()
            ):
                raise ValueError(
                    f"'{UNGROUPED_TORRENT_GROUP}' is reserved for torrents "
                    "without a group."
                )
            for torrent in torrents:
                existing_group = torrent_groups.get(torrent.hash)
                if existing_group is not None:
                    raise ValueError(
                        f"Torrent hash '{torrent.hash}' appears in both "
                        f"'{existing_group}' and '{group_name}'."
                    )
                torrent_groups[torrent.hash] = group_name
            normalized.setdefault(group_name, []).extend(torrents)
        return ordered_torrent_groups(normalized)

    def iter_torrents(self) -> Iterator[Torrent]:
        for torrents in self.torrents.values():
            yield from torrents

    @property
    def torrent_count(self) -> int:
        return sum(len(torrents) for torrents in self.torrents.values())

    def torrent_hashes_by_group(self) -> dict[str, list[str]]:
        return {
            group_name: [torrent.hash for torrent in torrents]
            for group_name, torrents in self.torrents.items()
        }


class Database(BaseModel):
    entries: list[Aggregate]
