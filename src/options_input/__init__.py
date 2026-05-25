"""Paste-based options input for the dashboard.

`parser.parse_options_text(text)` handles brokerage clipboard pastes (Schwab,
Robinhood, Tastytrade, free-form key/value). Returns a `ParsedOptions`
struct with strike/premium/expiry/etc — None for fields it couldn't extract.
Feeds into the OptionsStructure / KillSheetRequest fields.
"""
from options_input.parser import ParsedOptions, parse_options_text

__all__ = ["ParsedOptions", "parse_options_text"]
