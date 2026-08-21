import { useCallback, useEffect, useRef, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ApiError } from "./api";

/**
 * Fetch-with-cache, one hook for every screen.
 *
 * Two things a planner on a phone has to survive: opening on the bus with
 * no signal, and a school that has not switched a feature on. So a
 * successful response is written to AsyncStorage and replayed on the next
 * open — the screen shows last night's list with a "saved from…" notice
 * rather than a spinner into an error — and a 404 is reported separately
 * from a failure, because "your school hasn't enabled this" and "something
 * broke" deserve different screens.
 */

export type QueryState<T> = {
  data: T | null;
  error: string | null;
  /** The endpoint answered 404: the feature is off, not broken. */
  missing: boolean;
  loading: boolean;
  refreshing: boolean;
  /** Data came from the cache because the network attempt failed. */
  stale: boolean;
  reload: () => Promise<void>;
  refresh: () => Promise<void>;
  setData: (updater: (prev: T | null) => T | null) => void;
};

const PREFIX = "ip_cache_";

export async function clearQueryCache(): Promise<void> {
  const keys = await AsyncStorage.getAllKeys();
  const ours = keys.filter((k) => k.startsWith(PREFIX));
  if (ours.length) await AsyncStorage.multiRemove(ours);
}

export function useQuery<T>(
  cacheKey: string,
  fetcher: () => Promise<T>,
  opts: { enabled?: boolean } = {},
): QueryState<T> {
  const enabled = opts.enabled !== false;
  const [data, setDataState] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [refreshing, setRefreshing] = useState(false);
  const [stale, setStale] = useState(false);

  // The fetcher is usually an inline arrow, so it is a new function every
  // render. Holding it in a ref keeps `run` stable — otherwise the effect
  // below re-fires on every render and the screen fetches in a loop.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(
    async (mode: "initial" | "refresh") => {
      if (!enabled) return;
      if (mode === "refresh") setRefreshing(true);
      try {
        const fresh = await fetcherRef.current();
        if (!alive.current) return;
        setDataState(fresh);
        setError(null);
        setMissing(false);
        setStale(false);
        AsyncStorage.setItem(
          PREFIX + cacheKey,
          JSON.stringify({ at: Date.now(), value: fresh }),
        ).catch(() => {});
      } catch (e) {
        if (!alive.current) return;
        const err = e as ApiError;
        if (err?.status === 404) {
          setMissing(true);
          setError(null);
        } else {
          setError(err?.message || "Something went wrong.");
          // A cached copy is more useful than an error page, but only on
          // the first load — replacing what the student is already looking
          // at with older data during a pull-to-refresh would be a
          // regression they did not ask for.
          if (mode === "initial") {
            try {
              const raw = await AsyncStorage.getItem(PREFIX + cacheKey);
              if (raw && alive.current) {
                const parsed = JSON.parse(raw);
                setDataState(parsed.value as T);
                setStale(true);
              }
            } catch {
              /* a corrupt cache entry is not worth surfacing */
            }
          }
        }
      } finally {
        if (alive.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [cacheKey, enabled],
  );

  useEffect(() => {
    if (enabled) run("initial");
    else setLoading(false);
  }, [run, enabled]);

  const setData = useCallback((updater: (prev: T | null) => T | null) => {
    setDataState((prev) => updater(prev));
  }, []);

  return {
    data,
    error,
    missing,
    loading,
    refreshing,
    stale,
    reload: () => run("initial"),
    refresh: () => run("refresh"),
    setData,
  };
}
