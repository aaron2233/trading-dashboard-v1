"""Manual + screenshot options input for the dashboard.

`parser.parse_options_text(text)` handles brokerage clipboard pastes (Schwab,
Robinhood, Tastytrade, free-form key/value). Returns a `ParsedOptions`
struct with strike/premium/expiry/etc — None for fields it couldn't extract.

Screenshot extraction lives in `src/vision/options_extractor.py` —
`extract_options_chain(image_bytes=..., media_type=...)` runs Anthropic
vision on the image and returns the same shape (without `source_fields`).

Both feed into the existing OptionsStructure / KillSheetRequest fields.
"""
from options_input.parser import ParsedOptions, parse_options_text

__all__ = ["ParsedOptions", "parse_options_text"]
