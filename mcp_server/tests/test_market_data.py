from mcp_server.adapters.a_stock_data import parse_tencent_quote_response


def test_tencent_parser_extracts_quote_fields_and_quote_time():
    values = [""] * 53
    values[1] = "红利ETF"
    values[3] = "1.234"
    values[4] = "1.200"
    values[30] = "20260810113000"
    values[31] = "0.034"
    values[32] = "2.83"
    values[37] = "12345.6"
    values[38] = "0.42"
    values[39] = "8.1"
    values[45] = "100.0"
    values[46] = "0.86"
    raw = 'v_sh512890="{}~{}";'.format("~".join(values), "")

    result = parse_tencent_quote_response(raw, {"sh512890": "512890"})

    assert result["512890"]["name"] == "红利ETF"
    assert result["512890"]["price"] == 1.234
    assert result["512890"]["change_pct"] == 2.83
    assert result["512890"]["pe_ttm"] == 8.1
    assert result["512890"]["quote_time"] == "20260810113000"
