from mcp_server.adapters.sentiment import AStockDataSentimentProvider


class FakeInstrumentProvider:
    provider_id = "a-stock-data"
    skill_name = "a-stock-data"
    skill_version = "3.6.0"

    def collect(self, instrument, sections, as_of=None, refresh=False):
        assert instrument["code"] == "512890"
        assert list(sections) == ["news"]
        return {
            "news": {
                "data": [
                    {
                        "title": "测试新闻",
                        "content": "新闻摘要",
                        "time": "2026-08-11T10:00:00+08:00",
                        "source": "fixture-news",
                        "url": "https://example.test/news/1",
                    }
                ],
                "provenance": {
                    "source_name": "fixture-news",
                    "source_url": "https://example.test/search",
                },
                "status": "ok",
            }
        }


def test_a_stock_data_sentiment_provider_maps_news_without_raw_content():
    provider = AStockDataSentimentProvider(FakeInstrumentProvider())
    result = provider.collect_documents(
        {"source_id": "eastmoney-news", "platform": "eastmoney", "source_type": "news"},
        code="512890",
        market="SH",
        instrument_type="ETF",
    )

    assert result["warnings"] == []
    document = result["documents"][0]
    assert document["document_type"] == "news"
    assert document["targets"] == ["512890"]
    assert document["content_hash"]
    assert "content" not in document
    assert document["metadata"]["skill_name"] == "a-stock-data"


def test_restricted_sources_are_explicitly_manual():
    provider = AStockDataSentimentProvider(FakeInstrumentProvider())
    result = provider.collect_documents(
        {
            "source_id": "xueqiu-author-1",
            "platform": "xueqiu",
            "source_type": "blogger",
            "author_id": "author-1",
        },
        code="512890",
        market="SH",
        instrument_type="ETF",
    )

    assert result["documents"] == []
    assert "manual" in result["warnings"][0]
