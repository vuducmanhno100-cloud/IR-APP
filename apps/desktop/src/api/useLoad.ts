import { useCallback, useEffect, useState } from "react";
import { EngineError } from "./client";

export type LoadState<T> = {
  data: T | undefined;
  error: EngineError | Error | undefined;
  loading: boolean;
  reload: () => void;
};

export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): LoadState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<EngineError | Error>();
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    load()
      .then((value) => {
        if (active) {
          setData(value);
          setError(undefined);
        }
      })
      .catch((err: Error) => active && setError(err))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [...deps, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}
