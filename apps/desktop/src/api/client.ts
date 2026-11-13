import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type ErrorBody = Schemas["ErrorBody"];

export class EngineError extends Error {
  constructor(public readonly body: ErrorBody, public readonly status: number) {
    super(body.message);
  }
}

/** The engine could not be started or stopped answering; the shell can restart it. */
export class EngineDownError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EngineDownError";
  }
}

type Endpoint = { base_url: string; token: string };
type EngineClient = ReturnType<typeof createClient<paths>>;

const inTauri = "__TAURI_INTERNALS__" in window;
/** Only the desktop shell owns the engine process; the browser dev setup talks to a mock or a manual engine. */
export const canRestartEngine = inTauri;

const listeners = new Set<(error: EngineError) => void>();
const mockPrefer: string | undefined = inTauri ? undefined : import.meta.env.VITE_MOCK_PREFER;
let clientPromise: Promise<EngineClient> | null = null;

function message(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

async function invokeEndpoint(command: "engine_endpoint" | "engine_restart"): Promise<Endpoint> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<Endpoint>(command);
}

async function resolveEndpoint(): Promise<Endpoint> {
  if (inTauri) return invokeEndpoint("engine_endpoint");
  return {
    base_url: import.meta.env.VITE_ENGINE_URL ?? "http://127.0.0.1:4010",
    token: import.meta.env.VITE_ENGINE_TOKEN ?? "dev",
  };
}

function connect(endpoint: Endpoint): EngineClient {
  const client = createClient<paths>({ baseUrl: endpoint.base_url });
  client.use({
    onRequest({ request }) {
      request.headers.set("Authorization", "Bearer " + endpoint.token);
      if (mockPrefer) request.headers.set("Prefer", mockPrefer);
      return request;
    },
  });
  return client;
}

export function engine(): Promise<EngineClient> {
  // A failed start must not stick: the next call asks the shell again.
  clientPromise ??= resolveEndpoint()
    .then(connect)
    .catch((err: unknown) => {
      clientPromise = null;
      throw new EngineDownError(message(err));
    });
  return clientPromise;
}

export async function restartEngine(): Promise<void> {
  clientPromise = null;
  if (!inTauri) return;
  try {
    clientPromise = Promise.resolve(connect(await invokeEndpoint("engine_restart")));
  } catch (err) {
    throw new EngineDownError(message(err));
  }
}

export function onEngineError(listener: (error: EngineError) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function call<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await request;
  } catch (err) {
    // fetch only throws when nothing answered: the engine stopped or its port changed.
    clientPromise = null;
    throw new EngineDownError(`Mất kết nối tới engine (${message(err)})`);
  }
  const { data, error, response } = result;
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
