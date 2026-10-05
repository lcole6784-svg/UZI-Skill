"""A/H market universe loading and hard filters."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from .models import StockSnapshot


_COLUMN_ALIASES = {
    "code": ("代码", "symbol", "代码编号"),
    "name": ("名称", "中文名称", "name", "股票名称"),
    "price": ("最新价", "现价", "price", "最新"),
    "change_pct": ("涨跌幅", "涨幅", "change_pct"),
    "amount": ("成交额", "amount", "成交金额"),
    "industry": ("所属行业", "行业", "industry"),
    "open_price": ("今开", "开盘", "open"),
    "prev_close": ("昨收", "昨结", "prev_close"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "turnover_rate": ("换手率", "turnover_rate"),
    "volume_ratio": ("量比", "volume_ratio"),
    "market_cap": ("总市值", "market_cap"),
}


def _value(row: Any, key: str, default=None):
    for column in _COLUMN_ALIASES[key]:
        if column in row and row[column] is not None:
            return row[column]
    return default


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _full_code(code: Any, market: str) -> str:
    text = str(code or "").strip().lower()
    if not text:
        return ""

    # EastMoney usually returns bare numeric codes while Sina A-share spot
    # returns exchange-prefixed symbols such as sh600000/sz000001/bj430017.
    if "." in text:
        text = text.split(".", 1)[0]

    explicit_suffix = None
    for prefix, suffix in (("sh", "SH"), ("sz", "SZ"), ("bj", "BJ"), ("hk", "HK")):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            text = text[len(prefix):]
            explicit_suffix = suffix
            break

    raw = text
    if not raw.isdigit():
        return ""
    if market == "H":
        return f"{raw.zfill(5)}.HK"
    if len(raw) != 6:
        return ""
    if explicit_suffix in ("SH", "SZ", "BJ"):
        suffix = explicit_suffix
    else:
        suffix = "BJ" if raw.startswith(("4", "8", "92")) else ("SH" if raw.startswith(("5", "6", "9")) else "SZ")
    return f"{raw.zfill(6)}.{suffix}"


def normalize_universe_frame(frame, market: str, observed_at: str, source: str) -> list[StockSnapshot]:
    market = market.upper()
    snapshots: list[StockSnapshot] = []
    if frame is None or getattr(frame, "empty", True):
        return snapshots
    for _, row in frame.iterrows():
        name = str(_value(row, "name", "") or "").strip()
        upper_name = name.upper()
        price = _number(_value(row, "price"))
        amount = _number(_value(row, "amount"))
        code = _full_code(_value(row, "code"), market)
        change_pct = _number(_value(row, "change_pct"))
        if not code or not name or price is None or price <= 0 or amount is None or change_pct is None:
            continue
        if market == "A" and (upper_name.startswith(("ST", "*ST", "SST")) or "退" in name):
            continue
        if market == "H" and ("退市" in name or "停牌" in name):
            continue
        snapshots.append(StockSnapshot(
            code=code,
            name=name,
            market=market,
            price=price,
            change_pct=change_pct,
            amount=amount,
            industry=str(_value(row, "industry", "未分类") or "未分类"),
            open_price=_number(_value(row, "open_price")),
            prev_close=_number(_value(row, "prev_close")),
            high=_number(_value(row, "high")),
            low=_number(_value(row, "low")),
            turnover_rate=_number(_value(row, "turnover_rate")),
            volume_ratio=_number(_value(row, "volume_ratio")),
            market_cap=_number(_value(row, "market_cap")),
            observed_at=observed_at,
            source=source,
        ))
    return snapshots


def apply_hard_filters(stocks: list[StockSnapshot], min_turnover_local: float) -> tuple[list[StockSnapshot], dict]:
    if not math.isfinite(min_turnover_local) or min_turnover_local < 2e8:
        raise ValueError("minimum turnover must be finite and at least 200 million")
    kept = [stock for stock in stocks if stock.amount >= min_turnover_local]
    return kept, {
        "input": len(stocks),
        "liquid": len(kept),
        "removed_low_turnover": len(stocks) - len(kept),
        "min_turnover_local": min_turnover_local,
    }


def fetch_market_universe(market: str) -> list[StockSnapshot]:
    """Fetch an A/H whole-market snapshot with an independent-source fallback.

    EastMoney remains the primary source because it is richer and faster when
    reachable. Codex/overseas containers sometimes receive 5xx responses from
    EastMoney, so fall back to AKShare's Sina whole-market endpoints instead
    of turning the entire daily screen into an empty result.
    """
    import akshare as ak

    market = market.upper()
    if market == "A":
        providers = (
            ("stock_zh_a_spot_em", ak.stock_zh_a_spot_em),
            ("stock_zh_a_spot", ak.stock_zh_a_spot),
        )
    elif market == "H":
        providers = (
            ("stock_hk_spot_em", ak.stock_hk_spot_em),
            ("stock_hk_spot", ak.stock_hk_spot),
        )
    else:
        raise ValueError(f"daily screen only supports A/H markets, got {market!r}")

    errors = []
    for name, provider in providers:
        try:
            frame = provider()
            observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
            snapshots = normalize_universe_frame(frame, market, observed_at, f"akshare:{name}")
            if snapshots:
                return snapshots
            errors.append(f"{name}:empty_or_unrecognized")
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}:{str(exc)[:120]}")

    raise RuntimeError(f"all {market} universe providers failed: {'; '.join(errors)}")
