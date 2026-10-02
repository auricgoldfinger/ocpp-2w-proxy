# ocpp-2w-proxy

A 2 way OCPP proxy (can also be used as a 1-way simple proxy).

ocpp-2w-proxy allows chargers (one or more) to establish connections not just with one central management system (server), but to two.
This is useful for example if a setup requires that the charger wants to connect to an official server (e.g. for billing purposes), 
and at the same time the happy EV person would like to also view and control the charger from e.g. Home Assistant.

The proxy works be defining a primary and a secondary server. This governs the way in which the proxy will forward messages upstream
and downstream.

The rules are as follows:
1. All Calls (OCPP type 2) from the charger is forwarded to both the primary and the secondary server.
2. All Replies (OCPP type 3) or Errors (OCPP type 4) received from the primary server are forwarded to the charger.
3. All Replies (OCPP type 3) or Errors (OCPP type 4) received from the secondary server are ignored and not forwarded to the charger.
4. All Calls (OCPP type 2) received from either the primary or secondary server is forward to the charger. The message_id is noted against the server.
5. All Replies (OCPP type 3) or Errors (OCPP type 4) received from the charger is forwarded to either the primary OR the secondary server depending on which one sent it (based on message_id noted in step 4).

The proxy will also keep a watch dog of stale connections. If a connection is not seen for more than `watchdog_stale` seconds, the connection will be closed and removed from the list.


## Setup

This project uses [uv](https://docs.astral.sh/uv/) to manage Python and dependencies.

1. Install uv (see the [installation docs](https://docs.astral.sh/uv/getting-started/installation/) for other options):

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Clone the repository and install the dependencies:

   ```sh
   git clone https://github.com/ocpp-balanz/ocpp-2w-proxy.git
   cd ocpp-2w-proxy
   uv sync
   ```

   `uv sync` creates a virtual environment in `.venv` and installs the locked dependencies from `uv.lock`.
   The project uses Python 3.14 (pinned in `.python-version`). If it is not installed, uv downloads it automatically.

To add or update dependencies, use `uv add <package>` / `uv lock --upgrade`. These update `pyproject.toml` and `uv.lock`.

## Usage

Review and update configuration in `ocpp-2w-proxy.ini`. Start the proxy with:

```sh
uv run ocpp-2w-proxy.py
```

A different configuration file can be given with `--config`:

```sh
uv run ocpp-2w-proxy.py --config /path/to/my-config.ini
```

## Docker

`Dockerfile` and `compose.yaml` files are included for completeness. The image is based on `python:3.14-slim` and installs
the locked dependencies with uv. Build the image and start it with:

```sh
docker build -t ocpp-2w-proxy .
docker compose up -d
```

 