import { useCallback, useEffect, useRef, useState } from "react";
import { EngineError } from "./client";

export type LoadState<T> = {
  data: T | undefined;
  error: EngineError | Error | undefined;
  loading: boolean;
  reload: () => void;
};

/** Loads on mount and when deps change. With refreshMs it also reloads quietly (no spinner) while the window is visible. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], options: { refreshMs?: number } = {}): LoadState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<EngineError | Error>();
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const quiet = useRef(false);

  useEffect(() => {
    let active = true;
    if (!quiet.current) setLoading(true);
    quiet.current = false;
    load()
      .then((value) => {
        if (active) {
          setData(value);
          setError(undefined);
        }
      })
      .catch((err: unknown) => active && setError(err instanceof Error ? err : new Error(String(err))))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [...deps, version]);

  useEffect(() => {
    if (!options.refreshMs) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      quiet.current = true;
      setVersion((v) => v + 1);
    }, options.refreshMs);
    return () => window.clearInterval(timer);
  }, [options.refreshMs]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}

export type Page<T> = { items: T[]; next_cursor?: string | null };

export type PagedState<T> = {
  items: T[];
  error: Error | undefined;
  loading: boolean;
  hasMore: boolean;
  loadMore: () => void;
  reload: () => void;
};

/** Cursor pages appended one after another; deps (filters) start over from the first page. */
export function usePaged<T>(fetchPage: (cursor: string | undefined) => Promise<Page<T>>, deps: unknown[]): PagedState<T> {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const generation = useRef(0);

  const fetchInto = (from: string | undefined, append: boolean) => {
    const mine = ++generation.current;
    setLoading(true);
    fetchPage(from)
      .then((page) => {
        if (mine !== generation.current) return;
        setItems((previous) => (append ? [...previous, ...page.items] : page.items));
        setCursor(page.next_cursor ?? null);
        setError(undefined);
      })
      .catch((err: unknown) => mine === generation.current && setError(err instanceof Error ? err : new Error(String(err))))
      .finally(() => mine === generation.current && setLoading(false));
  };

  useEffect(() => fetchInto(undefined, false), [...deps, version]);

  return {
    items,
    error,
    loading,
    hasMore: cursor !== null,
    loadMore: () => cursor && fetchInto(cursor, true),
    reload: () => setVersion((v) => v + 1),
  };
}
