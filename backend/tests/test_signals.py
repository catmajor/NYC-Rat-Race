from datetime import datetime, date

import duckdb

from app.signals import PointInTimeSignals


def test_noaa_signal_is_aligned_to_the_point_in_time(tmp_path) -> None:
    path = tmp_path / "72503014732_2019.csv"
    path.write_text(
        "STATION,DATE,WND,VIS,TMP,AA1\n"
        '72503014732,2019-10-01T12:00:00,"130,1,N,0041,1","008000,1,9,9","+0067,1","06,0060,3,1"\n',
        encoding="utf-8",
    )

    signals = PointInTimeSignals(
        noaa_files=[str(path)],
        start_date=date(2019, 10, 1),
        end_date=date(2019, 10, 1),
    )
    weather = signals.weather_at(datetime(2019, 10, 1, 8))

    assert weather["temperature_c"] == 6.7
    assert abs(weather["wind_mps"] - 4.1) < 1e-9
    assert weather["visibility_m"] == 8000.0
    assert weather["rain_mm"] == 6.0


def test_gdelt_signal_uses_the_previous_calendar_day(tmp_path) -> None:
    path = tmp_path / "gdelt_events_nyc_2019_10.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            CREATE TABLE events(
                SQLDATE BIGINT,
                ActionGeo_Lat DOUBLE,
                ActionGeo_Long DOUBLE,
                NumMentions BIGINT,
                NumSources BIGINT,
                NumArticles BIGINT,
                AvgTone DOUBLE,
                GoldsteinScale DOUBLE
            )
            """
        )
        connection.execute(
            "INSERT INTO events VALUES (20191001, 40.75, -73.99, 4, 2, 3, -1.0, 2.0)"
        )
        connection.execute(
            "COPY events TO '" + str(path).replace("'", "''") + "' (FORMAT PARQUET)"
        )
    finally:
        connection.close()

    signals = PointInTimeSignals(
        gdelt_files=[str(path)],
        start_date=date(2019, 10, 1),
        end_date=date(2019, 10, 2),
    )
    events = signals.events_at(datetime(2019, 10, 2, 8))

    assert events["event_count"] == 1.0
    assert events["event_count:midtown"] == 1.0
    assert events["news_volume"] == 4.0
