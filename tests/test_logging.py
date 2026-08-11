import logging


def test_access_log_emitted(client, caplog):
    with caplog.at_level(logging.INFO, logger="mindbasic.access"):
        resp = client.get("/health")
        assert resp.status_code == 200
    records = [r for r in caplog.records if r.name == "mindbasic.access"]
    assert records
    record = records[-1]
    assert record.extra["method"] == "GET"
    assert record.extra["path"] == "/health"
    assert record.extra["status"] == 200
    assert "traceId" in record.extra
    assert record.extra["durationMs"] >= 0
