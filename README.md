# 🪴 Bonsai

- A local catalog tool for managing aggregates.
- An **aggregate** groups one or more Bangumi subjects and their torrent hashes
  under a local short name and category; it is not necessarily a one-to-one
  subject mapping.
- An MCP server provides agents with an interface for managing the local
  collection.

## Setup

Install dependencies:

```bash
uv sync
```

Create a local `.env` file as needed:

```bash
DB_PATH=db.sqlite3
QBIT_HOST=http://localhost
QBIT_PORT=8080
QBIT_USERNAME=admin
QBIT_PASSWORD=adminadmin
BANGUMI_TOKEN=
SEARCH_LANCEDB_PATH=aggregate_search.lancedb
```

## Usage

Configure as an MCP server, OpenCode as an example:

```json
{
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        "bonsai-manager": {
            "command": [
                "uv",
                "run",
                "python",
                "src/main.py",
                "--",
                "mcp"
            ],
            "cwd": "<path to project root>",
            "enabled": true,
            "environment": {
                "UV_WORKING_DIR": "<path to project root>"
            },
            "type": "local"
        }
    }
}
```

On first use, initialize SQLite and the semantic search index:

```bash
uv run ./src/main.py -- search --rebuild-index
uv run ./src/main.py -- health
```

Database-backed MCP tools are gated until health checks pass. MCP clients can use
`check_health` for details and `rebuild_search_index` to initialize or repair the
index without CLI access.

The MCP server provides tools to:

- add and remove aggregates
- update Bangumi subjects and torrent hashes
- list aggregates using SQLite filters
- search aggregates semantically
- audit torrent mappings
- check health and rebuild the search index

The `bonsai://aggregates/` resource returns the current aggregate count.

Launch the interactive TUI:

```bash
uv run ./src/main.py -- tui
```

Search semantically:

```bash
uv run ./src/main.py -- search "query"
```

Other maintenance commands:

```bash
uv run ./src/main.py -- list
uv run ./src/main.py -- audit
uv run ./src/main.py -- db validate
uv run ./src/main.py -- db import-json --input path/to/legacy-db.json
uv run ./src/main.py -- serve
```

## Bonus

To enable OpenCode to directly interact with Bangumi,
[BangumiMCP](https://github.com/Ukenn2112/BangumiMCP) can be added:

```json
{
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        "bangumi": {
            "command": [
                "uv",
                "run",
                "python",
                "main.py"
            ],
            "enabled": true,
            "environment": {
                "UV_WORKING_DIR": "<path to BangumiMCP>"
            },
            "type": "local"
        }
    }
}
```

## Roadmap

- [ ] Database
  - [x] Migrate aggregate storage to SQLite
  - [x] Remove the JSON runtime backend
  - [x] Add legacy JSON import and database validation
  - [ ] Add schema migrations for future SQLite changes
- [ ] Search
  - [x] Add LanceDB-backed semantic search index
  - [x] Incrementally update the index after aggregate changes
  - [x] Add consistency health checks and explicit index repair
- [ ] MCP
  - [x] Expose aggregate management and semantic search tools
  - [x] Expose search index rebuilding and health checks
  - [x] Gate database tools until health checks pass
  - [x] Expose aggregate collection summary resource
- [ ] Bangumi integration
  - [ ] Synchronize missing Bangumi collections to local catalog
- [ ] Torrent management
  - [ ] [Prowlarr](https://github.com/Prowlarr/Prowlarr) integration
  - [ ] Torrent location management
