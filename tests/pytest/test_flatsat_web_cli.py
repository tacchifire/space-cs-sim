"""CLI binding choices reach the console without opening USB transports."""
from pathlib import Path

import pytest

from cuberange.flatsat import __main__ as cli
from cuberange.flatsat import web


@pytest.mark.parametrize("host", [None, "100.64.0.1"])
def test_serve_forwards_selected_host_without_usb_discovery(monkeypatch, tmp_path, host):
    calls = []
    monkeypatch.setattr(web, "serve", lambda **kwargs: calls.append(kwargs) or 0)

    def no_discovery():
        pytest.fail("Starting the console must not discover USB through the CLI path")

    monkeypatch.setattr(cli, "discover_ports", no_discovery)
    argv = ["serve", "--port", "0", "--data-dir", str(tmp_path)]
    if host is not None:
        argv += ["--host", host]
    assert cli.main(argv) == 0
    assert calls == [{"host": host or "127.0.0.1", "port": 0, "data_dir": Path(tmp_path)}]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.1", "localhost", "::1"])
def test_invalid_binding_is_rejected_before_server_start(monkeypatch, host):
    def no_start(**kwargs):
        pytest.fail("An invalid binding must not start the server")

    monkeypatch.setattr(web, "serve", no_start)
    with pytest.raises(SystemExit) as error:
        cli.main(["serve", "--host", host])
    assert error.value.code == 2
