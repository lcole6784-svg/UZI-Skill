"""Fixed-snapshot tests for the A/H daily screen."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))


def test_universe_filters_st_invalid_quote_and_low_turnover():
    from lib.daily_screen.universe import apply_hard_filters, normalize_universe_frame

    frame = pd.DataFrame([
        {"代码": "600001", "名称": "正常股份", "最新价": 10, "涨跌幅": 4, "成交额": 3e8, "所属行业": "电子"},
        {"代码": "600002", "名称": "*ST 测试", "最新价": 5, "涨跌幅": 2, "成交额": 4e8, "所属行业": "电子"},
        {"代码": "600003", "名称": "低流动", "最新价": 8, "涨跌幅": 3, "成交额": 1e8, "所属行业": "电子"},
        {"代码": "600004", "名称": "停牌样本", "最新价": 0, "涨跌幅": 0, "成交额": 3e8, "所属行业": "电子"},
    ])
    normalized = normalize_universe_frame(frame, "A", "2026-08-28T11:30:00+08:00", "fixture")
    kept, stats = apply_hard_filters(normalized, 2e8)
    assert [item.code for item in kept] == ["600001.SH"]
    assert stats["removed_low_turnover"] == 1


def test_a_universe_falls_back_to_sina_and_normalizes_prefixed_code(monkeypatch):
    import akshare as ak
    from lib.daily_screen.universe import fetch_market_universe

    monkeypatch.setattr(ak, "stock_zh_a_spot_em", Mock(side_effect=RuntimeError("502 Bad Gateway")))
    monkeypatch.setattr(ak, "stock_zh_a_spot", lambda: pd.DataFrame([{
        "代码": "sh600001", "名称": "正常股份", "最新价": 10, "涨跌幅": 4,
        "成交额": 3e8, "昨收": 9.6, "今开": 9.8, "最高": 10.1, "最低": 9.7,
    }]))

    stocks = fetch_market_universe("A")
    assert len(stocks) == 1
    assert stocks[0].code == "600001.SH"
    assert stocks[0].source == "akshare:stock_zh_a_spot"


def test_hk_universe_falls_back_to_sina_and_accepts_chinese_name(monkeypatch):
    import akshare as ak
    from lib.daily_screen.universe import fetch_market_universe

    monkeypatch.setattr(ak, "stock_hk_spot_em", lambda: pd.DataFrame())
    monkeypatch.setattr(ak, "stock_hk_spot", lambda: pd.DataFrame([{
        "代码": "00700", "中文名称": "腾讯控股", "最新价": 500, "涨跌幅": 3,
        "成交额": 30e8, "昨收": 490, "今开": 495, "最高": 505, "最低": 492,
    }]))

    stocks = fetch_market_universe("H")
    assert len(stocks) == 1
    assert stocks[0].code == "00700.HK"
    assert stocks[0].name == "腾讯控股"
    assert stocks[0].source == "akshare:stock_hk_spot"


def test_universe_raises_clear_error_when_all_sources_fail(monkeypatch):
    import akshare as ak
    from lib.daily_screen.universe import fetch_market_universe

    monkeypatch.setattr(ak, "stock_hk_spot_em", Mock(side_effect=RuntimeError("primary down")))
    monkeypatch.setattr(ak, "stock_hk_spot", Mock(side_effect=RuntimeError("fallback down")))

    with pytest.raises(RuntimeError, match=r"stock_hk_spot_em.*stock_hk_spot"):
        fetch_market_universe("H")


def test_hk_skips_f_personas_but_keeps_serenity():
    from lib.daily_screen.models import StockSnapshot
    from lib.daily_screen.personas import evaluate_f_personas, evaluate_serenity

    stock = StockSnapshot("00700.HK", "腾讯控股", "H", 500, 3, 30e8, industry="人工智能")
    f_verdicts = evaluate_f_personas(stock, {"theme_rank": 1, "leader_rank": 1, "breadth_pct": 70})
    serenity = evaluate_serenity(stock, {}, [{"grade": "B", "kind": "event"}])
    assert len(f_verdicts) == 24
    assert all(item.signal == "skip" and item.eligible is False for item in f_verdicts)
    assert serenity.eligible is True


def test_persona_specs_are_distinct():
    from lib.daily_screen.personas import F_PERSONAS

    contracts = {(item.must_have, item.veto_rules) for item in F_PERSONAS}
    assert len(F_PERSONAS) == 24
    assert len(contracts) >= 20


def test_ranker_does_not_fill_quota_below_confidence():
    from lib.daily_screen.models import ScreenCandidate, StockSnapshot
    from lib.daily_screen.ranker import rank_candidates

    def candidate(code, confidence):
        return ScreenCandidate(StockSnapshot(code, code, "A", 10, 3, 3e8), confidence, "watch_only", "", "", "", 1, 1, 70)

    picks, rejected = rank_candidates([candidate("A", 72), candidate("B", 69)], top_n=10)
    assert [item.snapshot.code for item in picks] == ["A"]
    assert [item.snapshot.code for item in rejected] == ["B"]


def test_daily_renderer_escapes_external_text(tmp_path):
    from lib.daily_screen.renderer import render_report

    payload = '<img src=x onerror="alert(1)">'
    report = {
        "filters": {"top_n": 10},
        "picks": [{
            "snapshot": {"name": payload, "code": "TEST", "market": "A", "change_pct": 3, "amount": 3e8, "industry": payload},
            "persona_verdicts": [], "serenity": None, "action": "watch_only", "research_confidence": 71,
            "theme_rank": 1, "leader_rank": 1, "why_now": payload, "invalidation": payload,
            "entry_condition": payload, "evidence": [], "risk_flags": [],
        }],
    }
    path = render_report(report, tmp_path / "index.html", tmp_path / "missing")
    html = path.read_text(encoding="utf-8")
    assert payload not in html
    assert "&lt;img" in html
