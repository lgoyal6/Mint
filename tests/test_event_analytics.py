from mint.backend.db.read_storage import (
    _event_analytics_payload,
    _radar_points,
    _ratio_pct,
)


def test_ratio_pct_guards_zero_denominator_and_rounds() -> None:
    assert _ratio_pct(5, 0) == 0
    assert _ratio_pct(0, 40) == 0
    assert _ratio_pct(12, 40) == 30
    assert _ratio_pct(1, 3) == 33


def test_payload_computes_auto_axes_and_rescales_manual_ratings() -> None:
    payload = _event_analytics_payload(
        event={"id": "e1", "name": "Demo Day", "event_date": None, "status": "past"},
        going=40,
        checked_in=12,
        p0_p1=10,
        scores={"engagement": 4, "content": 5},
        notes="great room",
    )

    auto = {axis["key"]: axis for axis in payload["auto"]}
    assert auto["turnout"]["value"] == 30
    assert auto["turnout"]["detail"] == "12/40 checked in"
    assert auto["audience_fit"]["value"] == 25
    assert auto["audience_fit"]["detail"] == "10/40 P0/P1 of attendees"

    radar = {point["axis"]: point for point in payload["radar"]}
    assert radar["Turnout"] == {"axis": "Turnout", "score": 30, "kind": "auto"}
    assert radar["Engagement"]["score"] == 80  # 4 * 20
    assert radar["Content"]["score"] == 100  # 5 * 20
    assert "Follow-up" not in radar  # unrated manual axis is omitted from the radar

    assert payload["manual"]["scores"] == {"engagement": 4, "content": 5, "follow_up": None}
    assert payload["manual"]["notes"] == "great room"
    assert payload["overall_score"] == round((30 + 25 + 80 + 100) / 4)


def test_payload_without_rating_has_only_auto_axes_and_guards_division() -> None:
    payload = _event_analytics_payload(
        event={"id": "e2", "name": "Meetup", "event_date": None, "status": "past"},
        going=0,
        checked_in=0,
        p0_p1=0,
        scores=None,
        notes=None,
    )

    assert payload["manual"]["scores"] is None
    assert [point["axis"] for point in payload["radar"]] == ["Turnout", "Audience Fit"]
    assert payload["auto"][0]["value"] == 0
    assert payload["overall_score"] == 0


def test_radar_points_ignores_non_numeric_and_missing_scores() -> None:
    points = _radar_points(turnout=50, audience_fit=60, scores={"engagement": 3, "content": None, "bogus": 9})
    by_axis = {point["axis"]: point["score"] for point in points}
    assert by_axis == {"Turnout": 50, "Audience Fit": 60, "Engagement": 60}
