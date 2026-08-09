import json

from mcp_server.cli import main


def test_cli_can_configure_and_list_a_local_watchlist(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["watchlist-add", "512890", "--instrument-type", "ETF"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "512890"

    assert main(["configure", "--wake-time", "12:00"]) == 0
    assert main(["watchlist-list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["instrument_type"] == "ETF"
