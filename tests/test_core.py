from __future__ import annotations

import math
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lab.buckets import bucket_for_value, parse_bucket, settle_value
from lab.notify import DASHBOARD_URL, build_discord_message
from lab.probability import bucket_probabilities, daily_member_maxima
from lab.scoring import brier_score, select_eval_snapshot
from lab.timeutil import ready_to_settle
from lab.trading import place_trades, settle_trades
from lab import sources


class BucketTests(unittest.TestCase):
    def test_parse_all_supported_shapes(self):
        self.assertEqual(parse_bucket("57°F or below"),
                         {"label": "57°F or below", "lo": None, "hi": 57})
        self.assertEqual(parse_bucket("58-59°F"),
                         {"label": "58-59°F", "lo": 58, "hi": 59})
        self.assertEqual(parse_bucket("19°C"),
                         {"label": "19°C", "lo": 19, "hi": 19})
        self.assertEqual(parse_bucket("28°C or higher"),
                         {"label": "28°C or higher", "lo": 28, "hi": None})

    def test_unrecognized_bucket_raises(self):
        with self.assertRaises(ValueError):
            parse_bucket("about 20°C")

    def test_settlement_rounding_and_bucket_assignment(self):
        fahrenheit = [parse_bucket(label) for label in
                      ("69°F or below", "70-71°F", "72-73°F", "74°F or higher")]
        celsius = [parse_bucket(label) for label in
                   ("29°C or below", "30°C", "31°C", "32°C or higher")]
        self.assertEqual(settle_value(72.5, "round"), 73)
        self.assertEqual(bucket_for_value(settle_value(72.5, "round"), fahrenheit),
                         "72-73°F")
        self.assertEqual(bucket_for_value(settle_value(71.4, "round"), fahrenheit),
                         "70-71°F")
        self.assertEqual(settle_value(36.9, "floor"), 36)
        self.assertEqual(settle_value(30.1, "floor"), 30)
        self.assertEqual(settle_value(29.9, "floor"), 29)
        self.assertNotEqual(settle_value(72.5, "round"), round(72.5))
        self.assertEqual(bucket_for_value(settle_value(30.1, "floor"), celsius), "30°C")

    def test_real_settlement_fixtures(self):
        hk = [parse_bucket(label) for label in
              ("29°C or below", "30°C", "31°C", "32-35°C", "36°C", "37°C or higher")]
        nyc = [parse_bucket(label) for label in
               ("69°F or below", "70-71°F", "72-73°F", "74-75°F", "76-77°F",
                "78°F or higher")]
        london = [parse_bucket(label) for label in
                  ("19°C or below", "20°C", "21°C", "22-24°C", "25°C or higher")]
        fixtures = [
            (36.9, "floor", hk, "36°C"), (31.5, "floor", hk, "31°C"),
            (30.1, "floor", hk, "30°C"), (72.0, "round", nyc, "72-73°F"),
            (77.0, "round", nyc, "76-77°F"), (21.0, "round", london, "21°C"),
            (25.0, "round", london, "25°C or higher"),
        ]
        for raw, rule, buckets, expected in fixtures:
            with self.subTest(raw=raw, expected=expected):
                self.assertEqual(bucket_for_value(settle_value(raw, rule), buckets), expected)


class ProbabilityTests(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        buckets = [parse_bucket(label) for label in
                   ("9°C or below", "10°C", "11°C", "12°C or higher")]
        probabilities = bucket_probabilities([9.5, 10.5, 11.5], buckets, "round", 1.0)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=9)

    def test_tight_kernel_concentrates_in_center_bucket(self):
        buckets = [parse_bucket(label) for label in
                   ("9°C or below", "10°C", "11°C or higher")]
        probabilities = bucket_probabilities([10.0] * 12, buckets, "round", 0.05)
        self.assertGreater(probabilities[1], 0.95)

    def test_dst_fallback_days_include_repeated_hour(self):
        cases = [
            (date(2026, 10, 25), "Europe/London", "01:00"),
            (date(2026, 11, 1), "America/New_York", "01:00"),
        ]
        for day, zone, duplicate_hour in cases:
            start = datetime.combine(day, datetime.min.time(), ZoneInfo(zone))
            times = []
            values = []
            for hour in range(24):
                times.append(f"{day.isoformat()}T{hour:02d}:00")
                values.append(float(hour))
                if f"{hour:02d}:00" == duplicate_hour:
                    times.append(f"{day.isoformat()}T{hour:02d}:00")
                    values.append(99.0)
            hourly = {"time": times,
                      "temperature_2m_member01_model": values,
                      "other": values}
            with self.subTest(day=day, zone=zone):
                self.assertEqual(len(times), 25)
                self.assertEqual(daily_member_maxima(hourly, day.isoformat()), [99.0])
                self.assertIsNotNone(start.utcoffset())


class TimingAndScoringTests(unittest.TestCase):
    def test_settlement_waits_two_hours_after_local_day(self):
        target = date(2026, 9, 20)
        london_end = datetime(2026, 9, 21, 0, 0, tzinfo=ZoneInfo("Europe/London"))
        self.assertFalse(ready_to_settle(target, "Europe/London",
                                        london_end.astimezone(timezone.utc) + timedelta(hours=1, minutes=59)))
        self.assertTrue(ready_to_settle(target, "Europe/London",
                                       london_end.astimezone(timezone.utc) + timedelta(hours=2)))

    def test_selects_last_snapshot_before_local_midnight(self):
        rows = [
            {"market": "nyc", "target_date": "2026-09-22", "snapshot_at": stamp}
            for stamp in ("2026-09-21T01:00:00Z", "2026-09-22T03:59:59Z",
                          "2026-09-22T04:00:00Z", "2026-09-22T12:00:00Z")
        ]
        chosen = select_eval_snapshot(rows, "nyc", date(2026, 9, 22),
                                      "America/New_York")
        self.assertEqual(chosen["snapshot_at"], "2026-09-22T03:59:59Z")

    def test_brier_perfect_and_uniform_eleven(self):
        self.assertEqual(brier_score({"a": 1.0, "b": 0.0}, "a"), 0.0)
        uniform = {str(index): 1 / 11 for index in range(11)}
        self.assertAlmostEqual(brier_score(uniform, "0"), 10 / 11)


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
        self.strategy = {"min_edge": .08, "min_ask": .03, "max_ask": .85, "stake": 10}

    def snapshot(self, target="2026-09-23", buckets=None):
        return {"market": "hong-kong", "target_date": target,
                "tz": "Asia/Hong_Kong", "buckets": buckets or []}

    def test_threshold_ask_range_duplicate_and_today(self):
        buckets = [
            {"label": "a", "p": .61, "best_ask": .53},
            {"label": "b", "p": .109, "best_ask": .03},
            {"label": "c", "p": .94, "best_ask": .86},
            {"label": "d", "p": .50, "best_ask": None},
        ]
        paper = []
        made = place_trades(paper, self.snapshot(buckets=buckets), self.strategy, self.now)
        self.assertEqual([trade["bucket"] for trade in made], ["a"])
        self.assertEqual(place_trades(paper, self.snapshot(buckets=buckets),
                                     self.strategy, self.now), [])
        self.assertEqual(place_trades([], self.snapshot("2026-09-22", buckets),
                                     self.strategy, self.now), [])

    def test_winning_and_losing_pnl(self):
        paper = [
            {"market": "nyc", "target_date": "2026-09-23", "bucket": "win",
             "shares": 20.0, "status": "open", "pnl": None},
            {"market": "nyc", "target_date": "2026-09-23", "bucket": "lose",
             "shares": 50.0, "status": "open", "pnl": None},
        ]
        settle_trades(paper, [{"market": "nyc", "target_date": "2026-09-23",
                               "our_bucket": "win"}], 10, "2026-09-24T00:00:00Z")
        self.assertEqual((paper[0]["status"], paper[0]["pnl"]), ("won", 10.0))
        self.assertEqual((paper[1]["status"], paper[1]["pnl"]), ("lost", -10))


class DiscordTests(unittest.TestCase):
    def test_message_sections_and_dashboard(self):
        configs = [{"key": "nyc", "name": "紐約 LGA", "unit": "F"}]
        settled = [{"market": "nyc", "target_date": "2026-09-21", "obs_value": 72,
                    "our_bucket": "72-73°F", "p_of_actual": .41, "hit": True,
                    "top_bucket": "72-73°F", "top_p": .41,
                    "market_mids": {"72-73°F": .33}}]
        summary = {"all": {"hits": 1, "n": 1, "brier": .19,
                           "market_brier": .21},
                   "paper": {"trades": 1, "wins": 1, "pnl": 8}}
        message = build_discord_message(
            datetime(2026, 9, 22, tzinfo=timezone.utc), configs, settled, [], [],
            summary, {"nyc": {"n": 58}}, [], [])
        self.assertTrue(message.startswith("```\n"))
        self.assertIn("━ 昨日結算 ━", message)
        self.assertIn("━ 明日機會(edge ≥ 8%)━", message)
        self.assertIn("━ 累計(紙上,最近 30 天)━", message)
        self.assertIn(DASHBOARD_URL, message)
        self.assertTrue(message.endswith("\n```"))


class HkoParsingTests(unittest.TestCase):
    def test_daily_extract_skips_summary_rows(self):
        payload = {"stn": {"data": [{"month": 9, "dayData": [
            ["01", " 997.6", "28.7", "27.4"],
            ["02", " 998.7", " 31.8 ", "27.8"],
            ["03", "1000.0", "***", "27.0"],
            ["Mean/Total", "1005.0", "31.0", "28.3"],
        ]}]}}

        class FakeClient:
            def json(self, url, params=None):
                return payload

        values = sources._hko_primary_month(FakeClient(), 2026, 9)
        self.assertEqual(values, {"2026-09-01": 28.7, "2026-09-02": 31.8})


if __name__ == "__main__":
    unittest.main()
