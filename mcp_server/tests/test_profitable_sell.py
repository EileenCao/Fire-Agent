from mcp_server.services.backtesting import PositionBook, PositionLot


def _lot(lot_id, price, quantity):
    return PositionLot(
        lot_id=lot_id,
        code="512890",
        buy_date="2026-01-01",
        available_date="2026-01-02",
        quantity=quantity,
        price=price,
        cost=price * quantity,
        source="SIGNAL_BUY",
    )


def test_profitable_book_sell_skips_loss_lots_and_can_sell_older_profit_lots():
    book = PositionBook("512890")
    book.add(_lot("profit-old", 1.0, 100))
    book.add(_lot("loss-middle", 2.0, 100))
    book.add(_lot("profit-new", 1.2, 100))

    assert book.available_profitable_quantity(1.5, "2026-01-03") == 200

    actual, allocations = book.sell_profitable(200, 1.5, "2026-01-03")

    assert actual == 200
    assert [item["lot_id"] for item in allocations] == [
        "profit-old",
        "profit-new",
    ]
    assert book.total_quantity() == 100
    assert book.lots[0].lot_id == "loss-middle"
