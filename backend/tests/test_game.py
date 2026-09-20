from app.game import INITIAL_ALLOCATION, GameSession, _model_allocation, _model_payout_factor


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


def test_operating_windows_advance_and_skip_closed_hours() -> None:
    session = GameSession()

    first = session.state()
    assert (first["day"], first["turn_in_day"], first["time_start"], first["time_end"]) == (
        1,
        1,
        "08:00",
        "11:00",
    )

    for expected_start, expected_end in (("11:00", "14:00"), ("14:00", "17:00"), ("17:00", "20:00")):
        session.advance(dict(session.idle_taxis))
        current = session.state()
        assert current["day"] == 1
        assert current["time_start"] == expected_start
        assert current["time_end"] == expected_end

    session.advance(dict(session.idle_taxis))
    next_day = session.state()
    assert (next_day["day"], next_day["turn_in_day"], next_day["time_start"], next_day["time_end"]) == (
        2,
        1,
        "08:00",
        "11:00",
    )


def test_bank_and_points_are_updated_from_each_completed_window() -> None:
    session = GameSession()
    starting_bank = session.currency

    result = session.advance(dict(session.idle_taxis))
    state = session.state()

    assert result["bank_before"] == starting_bank
    assert result["bank_after"] == result["bank_before"] + result["bank_change"]
    assert state["currency"] == result["bank_after"]
    assert state["score"] == state["currency"]
    assert state["points"] == state["currency"]
    assert session.score == session.currency
    assert result["total_score"] == result["bank_after"]
    assert result["score_gain"] == result["bank_change"]
    assert result["bank_change"] == round(result["model_aligned_revenue"] - result["reposition_cost"], 2)
    assert result["taxi_move_cost"] == result["reposition_cost"]
    assert result["model_payout_factor"] == _model_payout_factor(result["model_match_percentage"])
    assert state["turns_completed"] == 1
    assert result["bank_breakdown"]["taxi_move_cost"] == result["reposition_cost"]


def test_moving_taxis_applies_a_visible_bank_cost() -> None:
    session = GameSession()
    allocation = dict(session.idle_taxis)
    allocation["midtown"] -= 1
    allocation["downtown"] += 1

    result = session.advance(allocation)

    assert result["repositioned_taxis"] == 1
    assert result["reposition_cost"] == 4.25
    assert result["taxi_move_cost"] == 4.25
    assert result["bank_breakdown"]["taxi_move_cost"] == 4.25
    assert result["bank_change"] == round(result["model_aligned_revenue"] - 4.25, 2)


def test_model_distribution_accuracy_changes_bank_payout() -> None:
    matched_session = GameSession()
    model_allocation = _model_allocation(
        matched_session._profile["model_forecast"],
        matched_session.fleet_available,
    )
    matched = matched_session.advance(model_allocation)

    mismatched_session = GameSession()
    mismatched_allocation = dict(model_allocation)
    largest_zone = max(model_allocation, key=model_allocation.get)
    smallest_zone = min(model_allocation, key=model_allocation.get)
    mismatched_allocation[largest_zone], mismatched_allocation[smallest_zone] = (
        mismatched_allocation[smallest_zone],
        mismatched_allocation[largest_zone],
    )
    mismatched = mismatched_session.advance(mismatched_allocation)

    assert matched["model_match_percentage"] == 100
    assert mismatched["model_match_percentage"] < matched["model_match_percentage"]
    assert matched["model_payout_factor"] > mismatched["model_payout_factor"]
    assert matched["model_aligned_revenue"] == matched["gross_revenue"]
    assert mismatched["model_aligned_revenue"] < mismatched["gross_revenue"]


def test_final_state_and_last_result_are_for_the_twelfth_window() -> None:
    session = GameSession()
    total_captured = 0
    for _ in range(12):
        result = session.advance(dict(session.idle_taxis))
        total_captured += int(result["trips_captured"])

    state = session.state()
    assert state["completed"] is True
    assert state["game_over_reason"] == "turns_complete"
    assert state["turns_completed"] == 12
    assert state["day"] == 3
    assert state["turn_in_day"] == 4
    assert state["time_start"] == "17:00"
    assert state["time_end"] == "20:00"
    assert state["total_trips_captured"] == total_captured
    assert state["last_result"]["day"] == 3
    assert state["last_result"]["turn_in_day"] == 4
