"""Crypto multi-TF scanner — Module 9 from the original V1 spec.

Wires the existing symbol-agnostic indicator stack (MA Ribbon, Stochastic,
SQN) onto Crypto.com candlestick data and adds the trading-edge cross-TF
disagreement-resolution matrix for crypto-specific confluence ratings.

Live ticker data (last price / 24h change / volume) comes from Crypto.com's
public REST. Order book + execution-time data live with the brokerage UI —
same anti-stale discipline as the options-input pivot.
"""
from crypto.scanner import (
    COMMON_PAIRS,
    CRYPTO_TIMEFRAMES,
    Confluence,
    CryptoSetup,
    CryptoTicker,
    CryptoTimeframeRead,
    Direction,
    classify_crypto_confluence,
    scan_crypto_setup,
)

__all__ = [
    "COMMON_PAIRS",
    "CRYPTO_TIMEFRAMES",
    "Confluence",
    "CryptoSetup",
    "CryptoTicker",
    "CryptoTimeframeRead",
    "Direction",
    "classify_crypto_confluence",
    "scan_crypto_setup",
]
