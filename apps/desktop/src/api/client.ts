import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type ErrorBody = Schemas["ErrorBody"];

export class EngineError extends Error {
  constructor(
    public readonly body: ErrorBody,
    public readonly status: number,
  ) {
    super(body.message);
  }
}

type Endpoint = { base_url: string; token: string };
type EngineClient = ReturnType<typeof createClient<paths>>;

const listeners = new Set<(error: EngineError) => void>();
const mockPrefer: string | undefined = "__TAURI_INTERNALS__" in window ? undefined : import.meta.env.VITE_MOCK_PREFER;
let clientPromise: Promise<EngineClient> | null = null;

async function resolveEndpoint(): Promise<Endpoint> {
  if ("__TAURI_INTERNALS__" in window) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<Endpoint>("engine_endpoint");
  }
  return {
    base_url: import.meta.env.VITE_ENGINE_URL ?? "http://127.0.0.1:4010",
    token: import.meta.env.VITE_ENGINE_TOKEN ?? "dev",
  };
}

export function engine(): Promise<EngineClient> {
  clientPromise ??= resolveEndpoint().then((endpoint) => {
    const client = createClient<paths>({ baseUrl: endpoint.base_url });
    client.use({
      onRequest({ request }) {
        request.headers.set("Authorization", `Bearer ${endpoint.token}`);
        if (mockPrefer) request.headers.set("Prefer", mockPrefer);
        return request;
      },
    });
    return client;
  });
  return clientPromise;
}

export function onEngineError(listener: (error: EngineError) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function call<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || !response.ok) {
    const problem = error as { error?: ErrorBody; title?: string; validation?: { message: string }[] } | undefined;
    const body = problem?.error ?? {
      code: "INTERNAL_ERROR",
      message: problem?.validation?.map((v) => v.message).join("; ") || problem?.title || response.statusText || "Request failed",
      detail: {},
      retryable: false,
    };
    const failure = new EngineError(body, response.status);
    listeners.forEach((listener) => listener(failure));
    throw failure;
  }
  return data as T;
}
