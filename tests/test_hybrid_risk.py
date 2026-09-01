import unittest

from backend.hybrid_risk import classify_close_reason, evaluate_metrics


BASE = dict(
    spread=0.1,
    contract_size=100.0,
    lot=0.01,
    point=0.01,
    stops_level=0,
    base=150.0,
    tp_atr=1.0,
    sl_atr=2.5,
    min_tp_spreads=10.0,
    max_spread_pct=12.0,
    max_pos_risk_pct=10.0,
    xau_max_pos_risk_pct=5.0,
)


class HybridRiskTests(unittest.TestCase):
    def test_close_reason_classification(self):
        self.assertEqual(classify_close_reason(1, tp_reason=1, sl_reason=2), "tp")
        self.assertEqual(classify_close_reason(2, tp_reason=1, sl_reason=2), "sl")
        self.assertEqual(classify_close_reason(7, tp_reason=1, sl_reason=2, scenario="emergency_trend"), "emergency_trend")
        self.assertEqual(classify_close_reason(3, tp_reason=1, sl_reason=2, expert_reason=3), "bot_close")
        self.assertEqual(classify_close_reason(4, tp_reason=1, sl_reason=2, client_reason=4), "manual")
        self.assertEqual(classify_close_reason(9, tp_reason=1, sl_reason=2), "unknown")

    def test_xau_above_five_percent_is_skipped(self):
        r = evaluate_metrics(symbol="XAUUSD.s", atr=3.1, **BASE)
        self.assertFalse(r["ok"])
        self.assertIn("лимит 5.0%", r["reason"])

    def test_xau_within_cap_passes(self):
        r = evaluate_metrics(symbol="XAUUSD.s", atr=1.0, **BASE)
        self.assertTrue(r["ok"])
        self.assertEqual(r["risk_cap_pct"], 5.0)

    def test_indices_use_global_cap(self):
        r = evaluate_metrics(symbol="NAS100.s", atr=1.0, **BASE)
        self.assertTrue(r["ok"])
        self.assertEqual(r["risk_cap_pct"], 10.0)

    def test_bad_spread_is_skipped(self):
        args = dict(BASE)
        args["spread"] = 2.0
        args["atr"] = 1.0
        args["contract_size"] = 10.0
        args["min_tp_spreads"] = 5.0
        r = evaluate_metrics(symbol="GER40.s", **args)
        self.assertFalse(r["ok"])
        self.assertIn("спред", r["reason"])

    def test_missing_atr_is_skipped(self):
        r = evaluate_metrics(symbol="NAS100.s", atr=0.0, **BASE)
        self.assertFalse(r["ok"])
        self.assertIn("ATR", r["reason"])


if __name__ == "__main__":
    unittest.main()
