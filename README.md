# 🪴 Bonsai

- a local catalog tool for mapping between Bangumi subjects and qBittorrent.

- an MCP server enabling agentic workflows of managing local Anime collection

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
SEARCH_BACKEND=lancedb
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

Launch the interactive TUI:

```bash
uv run ./src/main.py -- tui
```

Rebuild the semantic search index:

```bash
uv run ./src/main.py -- search --rebuild-index
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
  - [ ] Add schema migrations for future SQLite changes
- [ ] Search
  - [x] Add LanceDB-backed semantic search index
- [ ] Bangumi integration
  - [ ] Synchronize missing Bangumi collections to local catalog
- [ ] Torrent management
  - [ ] [Prowlarr](https://github.com/Prowlarr/Prowlarr) integration
  - [ ] Torrent location management
