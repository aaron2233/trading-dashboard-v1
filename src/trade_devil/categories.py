"""The 8 kill categories.

Auto-derivable: 1 (Regime), 2 (Technical), 5 (Account Fit), 8 (Exit Clarity).
Data-gated (PASS with note until external data integrates): 3 (IV), 4 (Catalyst),
6 (Consensus), 7 (Correlation).

Each function takes the KillSheet and returns a CategoryResult.
"""
from __future__ import annotations

from typing import Iterable

from kill_sheet.model import KillSheet
from trade_devil.verdict import CategoryResult, Verdict


# Common ETF tickers exempt from the $15-50 single-stock price band rule
# [src: ~/CLAUDE.md account profile, "ETFs at any price"]. List is a heuristic;
# user can extend via config in a later story.
_ETF_TICKERS = {
    "SPY", "QQQ", "QQQM", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "LQD",
    "XLF", "XLE", "XLK", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC",
    "VOO", "VTI", "VEA", "VWO", "EEM", "EFA", "AGG", "BND",
    "USO", "UNG", "GDX", "GDXJ", "ARKK", "ARKW", "ARKG", "ARKF",
    "SOXL", "TQQQ", "SQQQ", "UPRO", "SPXU", "TZA", "TNA",
}


# ─── 1. Regime Mismatch ───────────────────────────────────────────────────────


def check_regime_mismatch(sheet: KillSheet) -> CategoryResult:
    direction = sheet.direction.lower()
    regime = (sheet.regime or "").lower()

    long_regimes_kill = {"strong_bear"}
    long_regimes_flag = {"bear", "neutral"}
    short_regimes_kill = {"strong_bull"}
    short_regimes_flag = {"bull", "neutral"}

    if direction == "long":
        if regime in long_regimes_kill:
            return CategoryResult(
                "Regime Mismatch", Verdict.KILL,
                f"Long calls into {regime} regime — fighting the macro environment",
            )
        if regime in long_regimes_flag:
            return CategoryResult(
                "Regime Mismatch", Verdict.FLAG,
                f"Long against {regime} regime — needs strong technical / catalyst confirmation",
            )
        return CategoryResult(
            "Regime Mismatch", Verdict.PASS,
            f"Long aligned with {regime} regime",
        )

    if direction == "short":
        if regime in short_regimes_kill:
            return CategoryResult(
                "Regime Mismatch", Verdict.KILL,
                f"Long puts into {regime} regime — rallies eat shorts here",
            )
        if regime in short_regimes_flag:
            return CategoryResult(
                "Regime Mismatch", Verdict.FLAG,
                f"Short against {regime} regime — exceptional setup required",
            )
        return CategoryResult(
            "Regime Mismatch", Verdict.PASS,
            f"Short aligned with {regime} regime",
        )

    return CategoryResult(
        "Regime Mismatch", Verdict.FLAG,
        f"Unknown direction {direction!r}; manual review required",
    )


# ─── 2. Technical Invalidation ────────────────────────────────────────────────


def check_technical_invalidation(sheet: KillSheet) -> CategoryResult:
    direction = sheet.direction.lower()
    stack = (sheet.ma_stack or "").lower()
    # Weekly-trigger sheets (qqqm-core, Track A) trade the weekly ribbon — a
    # tangled daily stack inside an intact weekly trend is consolidation, not
    # chop. Mirrors the kill-sheet builder's trigger-TF chop evaluation.
    if sheet.trigger_tf == "Weekly" and sheet.weekly_stack:
        stack = sheet.weekly_stack.lower()
    signal = (sheet.stoch_signal or "").lower()

    if stack in {"chop", "n/a", ""}:
        return CategoryResult(
            "Technical Invalidation", Verdict.KILL,
            "MA Ribbon is chop / no clear ordering — no trend, no trade",
        )

    bull_stacks = {"full_bull", "bull_developing"}
    bear_stacks = {"full_bear", "bear_developing"}
    bull_signals = {"bull_cross_oversold", "bull_continuation", "bullish_divergence"}
    bear_signals = {"bear_cross_overbought", "bear_continuation", "bearish_divergence"}
    counter_long_signals = {"bear_cross_overbought", "bearish_divergence"}
    counter_short_signals = {"bull_cross_oversold", "bullish_divergence"}

    if direction == "long" and stack in bear_stacks:
        return CategoryResult(
            "Technical Invalidation", Verdict.KILL,
            f"Long calls against {stack} — chart contradicts thesis",
        )
    if direction == "short" and stack in bull_stacks:
        return CategoryResult(
            "Technical Invalidation", Verdict.KILL,
            f"Long puts against {stack} — chart contradicts thesis",
        )

    if direction == "long" and signal in counter_long_signals:
        return CategoryResult(
            "Technical Invalidation", Verdict.FLAG,
            f"Stochastic {signal} fired against the long thesis",
        )
    if direction == "short" and signal in counter_short_signals:
        return CategoryResult(
            "Technical Invalidation", Verdict.FLAG,
            f"Stochastic {signal} fired against the short thesis",
        )

    if stack in {"compression"}:
        return CategoryResult(
            "Technical Invalidation", Verdict.FLAG,
            "MA Ribbon is compressed — wait for expansion / breakout direction",
        )
    if stack.endswith("_developing"):
        return CategoryResult(
            "Technical Invalidation", Verdict.FLAG,
            f"{stack} stack is forming, not fully confirmed",
        )

    aligned_signals = bull_signals if direction == "long" else bear_signals
    if signal in aligned_signals or signal == "neutral":
        return CategoryResult(
            "Technical Invalidation", Verdict.PASS,
            f"{stack} stack with stoch {signal or 'mid'} supports the thesis",
        )
    return CategoryResult(
        "Technical Invalidation", Verdict.PASS,
        f"{stack} stack supports the thesis",
    )


# ─── 3. IV/Premium Overpricing ────────────────────────────────────────────────


def check_iv_premium_overpricing(sheet: KillSheet) -> CategoryResult:
    if sheet.options is None:
        return CategoryResult(
            "IV/Premium Overpricing", Verdict.PASS,
            "Skipped: no options data on this kill sheet (Standard template). "
            "Pass --strike/--premium/--iv-rank for full IV scrutiny. "
            "Verify manually — IV Rank >50% is a flag; >80% is a kill.",
        )

    o = sheet.options
    contract_cost = o.premium * 100.0
    pct_of_account = (
        contract_cost / sheet.account_balance_usd if sheet.account_balance_usd else 0.0
    )

    # Hard kills first [src: trade-devil/SKILL.md:114-128]
    if o.iv_rank is not None and o.iv_rank > 80:
        return CategoryResult(
            "IV/Premium Overpricing", Verdict.KILL,
            f"IV Rank {o.iv_rank:.0f}% — options extremely expensive, "
            "buying peak-inflated premium",
        )
    if pct_of_account > 0.05:
        return CategoryResult(
            "IV/Premium Overpricing", Verdict.KILL,
            f"Premium ${contract_cost:.2f}/contract is {pct_of_account:.1%} of account "
            f"(>5% ceiling)",
        )
    if o.bid_ask_spread is not None and o.premium > 0:
        spread_pct = o.bid_ask_spread / o.premium
        if spread_pct > 0.10:
            return CategoryResult(
                "IV/Premium Overpricing", Verdict.KILL,
                f"Bid-ask spread ${o.bid_ask_spread:.2f} is {spread_pct:.1%} of premium "
                "(>10% — illiquidity tax kills the edge)",
            )

    # Accumulate flags
    flag_notes: list[str] = []
    if o.iv_rank is not None and o.iv_rank > 50:
        flag_notes.append(f"IV Rank {o.iv_rank:.0f}% (moderately elevated)")
    if pct_of_account > 0.03:
        flag_notes.append(f"Premium {pct_of_account:.1%} of account (3-5% band)")

    if flag_notes:
        return CategoryResult(
            "IV/Premium Overpricing", Verdict.FLAG, "; ".join(flag_notes)
        )

    iv_str = f"IV Rank {o.iv_rank:.0f}%" if o.iv_rank is not None else "IV Rank n/a"
    return CategoryResult(
        "IV/Premium Overpricing", Verdict.PASS,
        f"{iv_str}, premium ${contract_cost:.2f} ({pct_of_account:.1%} of account)",
    )


# ─── 4. Catalyst Timing ───────────────────────────────────────────────────────


def check_catalyst_timing(sheet: KillSheet) -> CategoryResult:
    return CategoryResult(
        "Catalyst Timing", Verdict.PASS,
        "Skipped: earnings + news data not yet integrated. Verify manually — "
        "earnings within 5 trading days = kill (unless explicit earnings play).",
    )


# ─── 5. Account Fit ───────────────────────────────────────────────────────────


def check_account_fit(sheet: KillSheet) -> CategoryResult:
    price = sheet.close_at_generation
    ticker = (sheet.ticker or "").upper()
    is_etf = ticker in _ETF_TICKERS

    # Single-stock price band is $15-50 [src: ~/CLAUDE.md account profile]
    if not is_etf and (price < 15.0 or price > 50.0):
        return CategoryResult(
            "Account Fit", Verdict.KILL,
            f"{ticker} at ${price:,.2f} is outside the $15-50 single-stock band "
            f"(ETFs are exempt; {ticker} is not on the ETF list).",
        )

    # 2-3% high conviction, 1-2% medium, 0.5-1% spec → max_risk_usd should be in budget
    risk_pct = sheet.risk_pct
    if risk_pct > 0.03:
        return CategoryResult(
            "Account Fit", Verdict.FLAG,
            f"Risk per trade is {risk_pct:.1%} — exceeds the 3% high-conviction ceiling.",
        )

    # Premium-on-the-edge flag (high end of risk budget)
    if sheet.max_risk_usd >= 0.025 * sheet.account_balance_usd:
        return CategoryResult(
            "Account Fit", Verdict.PASS,
            f"Risk ${sheet.max_risk_usd:,.0f} on ${sheet.account_balance_usd:,.0f} "
            f"({risk_pct:.1%}) — at high-conviction ceiling but within rules.",
        )

    return CategoryResult(
        "Account Fit", Verdict.PASS,
        f"Risk ${sheet.max_risk_usd:,.0f} ({risk_pct:.1%}) within account rules; "
        f"{'ETF' if is_etf else f'price ${price:,.2f}'} qualifies.",
    )


# ─── 6. Consensus/Crowding ────────────────────────────────────────────────────


def check_consensus_crowding(sheet: KillSheet) -> CategoryResult:
    return CategoryResult(
        "Consensus/Crowding", Verdict.PASS,
        "Skipped: analyst ratings + flow data not yet integrated. Verify manually — "
        "price > avg analyst target = kill; 90%+ Buy ratings = contrarian kill.",
    )


# ─── 7. Correlation Trap ──────────────────────────────────────────────────────


def check_correlation_trap(sheet: KillSheet,
                           open_positions: list | None = None) -> CategoryResult:
    if open_positions is None:
        return CategoryResult(
            "Correlation Trap", Verdict.PASS,
            "Skipped: no position store passed to the devil. "
            "Run via kill_sheet CLI or pass open_positions to run_devil.",
        )

    same_ticker = [p for p in open_positions if p.ticker == sheet.ticker.upper()]
    if same_ticker:
        # Bucket by THESIS, not contract direction: every long-options position
        # is direction="long" regardless of call/put, so a raw direction compare
        # inverted the verdict whenever the open leg was a put — it KILLed
        # legitimate hedges and only soft-FLAGged actual doubling-down. The
        # sheet's direction is the thesis ("long"=bullish, "short"=bearish);
        # existing positions expose thesis via Position.thesis_direction.
        # (Fixed 2026-06.)
        sheet_thesis = "bullish" if sheet.direction.lower() == "long" else "bearish"
        same_thesis = [p for p in same_ticker if p.thesis_direction == sheet_thesis]
        if same_thesis:
            return CategoryResult(
                "Correlation Trap", Verdict.KILL,
                f"Already {len(same_thesis)} {sheet_thesis} position(s) open in "
                f"{sheet.ticker} — this would double down on the same thesis.",
            )
        # Opposite thesis = hedge (legitimate but worth flagging)
        return CategoryResult(
            "Correlation Trap", Verdict.FLAG,
            f"Already {len(same_ticker)} position(s) in {sheet.ticker} on the other "
            "side — confirm this is a deliberate hedge, not confusion.",
        )

    if not open_positions:
        return CategoryResult(
            "Correlation Trap", Verdict.PASS,
            "No open positions — no overlap risk.",
        )

    return CategoryResult(
        "Correlation Trap", Verdict.PASS,
        f"No same-ticker overlap with {len(open_positions)} open position(s). "
        "(Sector / macro correlation checks not yet implemented — verify manually.)",
    )


# ─── 8. Exit Clarity ──────────────────────────────────────────────────────────


def check_exit_clarity(sheet: KillSheet) -> CategoryResult:
    has_target = sheet.target_price is not None
    has_invalidation = sheet.invalidation_price is not None

    if not has_target and not has_invalidation:
        return CategoryResult(
            "Exit Clarity", Verdict.KILL,
            "Neither target nor invalidation defined — no trade plan, no entry.",
        )
    if not has_invalidation:
        return CategoryResult(
            "Exit Clarity", Verdict.KILL,
            "Invalidation level missing — you cannot trade without knowing where you're wrong.",
        )
    if not has_target:
        return CategoryResult(
            "Exit Clarity", Verdict.FLAG,
            "Target price missing — risk/reward unverifiable.",
        )

    # Both set — sanity check the R:R
    entry = sheet.close_at_generation
    if entry > 0:
        if sheet.direction.lower() == "long":
            reward = (sheet.target_price - entry) / entry
            risk = (entry - sheet.invalidation_price) / entry
        else:
            reward = (entry - sheet.target_price) / entry
            risk = (sheet.invalidation_price - entry) / entry

        if reward <= 0:
            return CategoryResult(
                "Exit Clarity", Verdict.KILL,
                f"Target {sheet.target_price} is on the wrong side of entry {entry}.",
            )
        if risk <= 0:
            return CategoryResult(
                "Exit Clarity", Verdict.KILL,
                f"Invalidation {sheet.invalidation_price} is on the wrong side of entry {entry}.",
            )
        rr = reward / risk
        if rr < 2.0:
            return CategoryResult(
                "Exit Clarity", Verdict.FLAG,
                f"R:R is {rr:.2f}:1 — below the 2:1 minimum for options trades.",
            )

    return CategoryResult(
        "Exit Clarity", Verdict.PASS,
        f"Target ${sheet.target_price:,.2f} and invalidation ${sheet.invalidation_price:,.2f} "
        "both defined; R:R within bounds.",
    )


ALL_CHECKS: Iterable = (
    check_regime_mismatch,
    check_technical_invalidation,
    check_iv_premium_overpricing,
    check_catalyst_timing,
    check_account_fit,
    check_consensus_crowding,
    check_correlation_trap,
    check_exit_clarity,
)
