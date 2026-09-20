from app.game import INITIAL_ALLOCATION, GameSession


def test_weather_biases_persist_and_advance_after_a_turn() -> None:
    session = GameSession()
    before = session.state()
    bias_before = before["weather_bias"]["midtown"]

    session.advance(INITIAL_ALLOCATION)
    after = session.state()

    assert after["round"] == 2
    assert after["weather_bias"]["midtown"] == bias_before
    assert after["zones"]["midtown"]["weather"] != before["zones"]["midtown"]["weather"]
    assert after["model_name"] == "ONNX demand model"


def test_catalog_events_are_conditioned_and_region_specific() -> None:
    session = GameSession()
    news = session.state()["news"]

    assert news
    assert all(item["zone_id"] in INITIAL_ALLOCATION for item in news)
    assert any(item["event_id"] == "staten_ferry" for item in news)
