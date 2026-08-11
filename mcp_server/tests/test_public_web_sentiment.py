from mcp_server.adapters.public_web_sentiment import PublicWebSentimentProvider


class FakeResponse:
    text = """
    <rss><channel><item>
      <title>公开观点</title>
      <link>https://example.test/post/1</link>
      <pubDate>Tue, 11 Aug 2026 10:00:00 +0800</pubDate>
      <description>512890 公开观点偏强</description>
    </item></channel></rss>
    """

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, url, timeout):
        assert url == "https://example.test/feed"
        assert timeout == 15
        return FakeResponse()


def test_public_web_provider_reads_explicit_feed_and_keeps_provenance():
    provider = PublicWebSentimentProvider(session=FakeSession())
    result = provider.collect_documents(
        {
            "source_id": "public-author",
            "platform": "xueqiu",
            "source_type": "blogger",
            "author_id": "author-1",
            "display_name": "公开作者",
            "config": {"url": "https://example.test/feed"},
        },
        code="512890",
        market="SH",
        instrument_type="ETF",
    )

    assert result["warnings"] == []
    document = result["documents"][0]
    assert document["document_type"] == "blogger"
    assert document["metadata"]["provider_id"] == "public-web"
    assert "content" not in document


def test_public_web_provider_requires_explicit_url():
    result = PublicWebSentimentProvider(session=FakeSession()).collect_documents(
        {"source_id": "public", "platform": "web", "source_type": "news"},
        code="512890",
        market="SH",
    )

    assert result["documents"] == []
    assert result["warnings"]
