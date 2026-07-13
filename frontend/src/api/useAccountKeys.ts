import { useEffect, useState } from "react";
import { api } from "./client";

/** Sleeve keys for new-position / kill-sheet account dropdowns.
 * Single source of truth: GET /api/v1/accounts/keys (config accounts minus
 * pool members). The fallback covers offline/initial render only. */
const FALLBACK_KEYS = ["main", "lotto"];

export function useAccountKeys(): string[] {
  const [keys, setKeys] = useState<string[]>(FALLBACK_KEYS);
  useEffect(() => {
    let cancelled = false;
    api
      .accountKeys()
      .then((res) => {
        if (!cancelled && res.keys.length > 0) setKeys(res.keys);
      })
      .catch(() => {
        // Offline — keep the fallback; the form still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return keys;
}
