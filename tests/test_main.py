import socket

from ocpp_2w_proxy.__main__ import main

CONFIG = """
[proxy]
listen = "0.0.0.0"
port = {port}
state_dir = "{state}"

[[chargers]]
id = "CH1"

[primary]
url = "wss://primary.example"
"""


def _write(tmp_path, port):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.format(port=port, state=tmp_path))
    return str(path)


def test_healthcheck_uses_the_configured_port(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        config = _write(tmp_path, listener.getsockname()[1])  # not the default 8321

        assert main(["--config", config, "--healthcheck"]) == 0


def test_healthcheck_fails_when_nothing_listens(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert main(["--config", _write(tmp_path, free_port), "--healthcheck"]) == 1
