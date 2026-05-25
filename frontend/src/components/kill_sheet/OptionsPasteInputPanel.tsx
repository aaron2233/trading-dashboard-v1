import { useState } from "react";
import { api } from "../../api/client";
import type { ParsedOptionsResponse } from "../../api/types";

interface Props {
  ticker: string;
  onParseResult: (parsed: ParsedOptionsResponse) => void;
}

export function OptionsPasteInputPanel({ ticker, onParseResult }: Props) {
  const [pasteText, setPasteText] = useState("");
  const [extractLoading, setExtractLoading] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [extractWarnings, setExtractWarnings] = useState<string[]>([]);

  async function handleExtract() {
    if (!pasteText.trim()) return;
    setExtractLoading(true);
    setExtractError(null);
    try {
      const parsed = await api.extractOptionsText(pasteText, ticker || undefined);
      onParseResult(parsed);
      setExtractWarnings(parsed.warnings);
    } catch (err) {
      setExtractError(err instanceof Error ? err.message : String(err));
    } finally {
      setExtractLoading(false);
    }
  }

  return (
    <>
      <div className="panel-header">Options input — paste from brokerage</div>
      <div className="panel-body space-y-3">
        <p className="text-xs text-text-secondary">
          Brokerage data is fresher than any web feed. Paste options chain
          text from your platform — extracted fields prefill the form below
          with a "from paste" tag. Manual edits clear the tag.
        </p>
        <div>
          <label className="label">Paste options snapshot</label>
          <textarea
            className="input w-full font-mono text-xs"
            rows={5}
            placeholder={"Strike: 480\nPremium: 4.55\nIV Rank: 35\nOI: 12,500\nExpiry: 2026-06-19\nType: call"}
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              className="btn btn-secondary text-xs"
              disabled={extractLoading || !pasteText.trim()}
              onClick={handleExtract}
            >
              {extractLoading ? "Extracting…" : "Extract from paste"}
            </button>
            {pasteText && (
              <button
                type="button"
                className="btn text-xs"
                onClick={() => setPasteText("")}
              >
                Clear
              </button>
            )}
          </div>
        </div>
        {extractError && (
          <div className="text-sm text-signal-bear">{extractError}</div>
        )}
        {extractWarnings.length > 0 && (
          <ul className="text-xs text-signal-flag space-y-0.5">
            {extractWarnings.map((w, i) => (
              <li key={i}>⚠ {w}</li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
