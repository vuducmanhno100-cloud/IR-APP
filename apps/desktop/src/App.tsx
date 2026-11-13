import { useEffect, useState } from "react";
import { engine, onEngineError, type EngineError } from "./api/client";
import { ErrorView, Loading, Section } from "./components/common";
import { StatusBanner } from "./components/StatusBanner";
import { CaseDetail } from "./screens/CaseDetail";
import { EventDetail } from "./screens/EventDetail";
import { Health } from "./screens/Health";
import { Alerts, Cases, Unresolved } from "./screens/Lists";
import { ProcessTree } from "./screens/ProcessTree";
import { Search } from "./screens/Search";
import { Settings } from "./screens/Settings";
import { SyncOverview } from "./screens/SyncOverview";
import { Wizard } from "./screens/Wizard";

type Route =
  | { name: "health" | "wizard" | "sync" | "search" | "alerts" | "unresolved" | "cases" | "settings" }
  | { name: "event"; eventId: number }
  | { name: "tree"; eventId: number; processId?: number }
  | { name: "case"; caseId: number };

const NAV: { name: Route["name"]; label: string }[] = [
  { name: "search", label: "Tìm kiếm" },
  { name: "alerts", label: "Alert" },
  { name: "sync", label: "Đồng bộ" },
  { name: "unresolved", label: "Chưa liên kết" },
  { name: "cases", label: "Case & Evidence" },
  { name: "settings", label: "Cài đặt" },
  { name: "wizard", label: "Kết nối ELK" },
  { name: "health", label: "Engine" },
];

// Background problems (key, quota, TLS) come from StatusBanner; this one only appears when a request fails.
const BANNER_CODES = new Set(["DB_UNAVAILABLE"]);

function initialRoute(): Route {
  const params = new URLSearchParams(window.location.search);
  const screen = params.get("screen");
  const eventId = Number(params.get("event"));
  const processId = Number(params.get("process")) || undefined;
  const caseId = Number(params.get("case"));
  if (screen === "tree" && eventId) return { name: "tree", eventId, processId };
  if (screen === "event" && eventId) return { name: "event", eventId };
  if (screen === "case" && caseId) return { name: "case", caseId };
  if (screen && NAV.some((n) => n.name === screen)) return { name: screen } as Route;
  return { name: "search" };
}

function routeQuery(route: Route): string {
  const params = new URLSearchParams();
  params.set("screen", route.name);
  if (route.name === "event" || route.name === "tree") params.set("event", String(route.eventId));
  if (route.name === "tree" && route.processId) params.set("process", String(route.processId));
  if (route.name === "case") params.set("case", String(route.caseId));
  return params.toString();
}

function navSection(route: Route): Route["name"] {
  if (route.name === "case") return "cases";
  if (route.name === "event" || route.name === "tree") return "search";
  return route.name;
}

export function App() {
  const [route, setRoute] = useState<Route>(initialRoute);
  const [banner, setBanner] = useState<EngineError>();
  const [engineState, setEngineState] = useState<"starting" | "ready" | Error>("starting");

  const navigate = (next: Route) => {
    window.history.pushState(null, "", `${window.location.pathname}?${routeQuery(next)}`);
    setRoute(next);
  };

  useEffect(() => {
    engine()
      .then(() => setEngineState("ready"))
      .catch((err: Error) => setEngineState(err));
  }, []);

  useEffect(
    () =>
      onEngineError((err) => {
        if (BANNER_CODES.has(err.body.code)) setBanner(err);
      }),
    [],
  );

  useEffect(() => {
    const restore = () => setRoute(initialRoute());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const openEvent = (eventId: number) => navigate({ name: "event", eventId });
  const openTree = (eventId: number, processId?: number) => navigate({ name: "tree", eventId, processId });
  const openCase = (caseId: number) => navigate({ name: "case", caseId });
  const current = navSection(route);

  return (
    <div className="app">
      <nav aria-label="Điều hướng chính">
        <div className="brand">IR Desktop</div>
        {NAV.map((item) => (
          <button
            key={item.name}
            type="button"
            className={current === item.name ? "active" : ""}
            aria-current={current === item.name ? "page" : undefined}
            onClick={() => navigate({ name: item.name } as Route)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <main>
        {engineState === "starting" && (
          <Section title="Đang khởi động engine">
            <Loading when text="Đang khởi động engine và cơ sở dữ liệu. Lần mở đầu tiên có thể mất 10–30 giây." />
          </Section>
        )}
        {engineState instanceof Error && (
          <Section title="Không khởi động được engine">
            <ErrorView error={engineState} />
          </Section>
        )}
        {engineState === "ready" && (
          <>
            <StatusBanner routeKey={routeQuery(route)} onNavigate={(target) => navigate({ name: target })} />
            {banner && (
              <div className="banner bad" role="alert">
                <span>
                  <strong>{banner.body.code}</strong> {banner.message}
                </span>
                <button onClick={() => setBanner(undefined)}>Ẩn</button>
              </div>
            )}
            {route.name === "health" && <Health />}
            {route.name === "wizard" && <Wizard onSaved={() => navigate({ name: "sync" })} />}
            {route.name === "sync" && <SyncOverview />}
            {route.name === "search" && <Search onOpenEvent={openEvent} onTrace={openTree} />}
            {route.name === "alerts" && <Alerts onOpenEvent={openEvent} />}
            {route.name === "unresolved" && <Unresolved onOpenEvent={openEvent} />}
            {route.name === "cases" && <Cases onOpenCase={openCase} />}
            {route.name === "case" && <CaseDetail caseId={route.caseId} onOpenEvent={openEvent} onOpenTree={openTree} />}
            {route.name === "settings" && (
              <Settings onReconfigure={() => navigate({ name: "wizard" })} onDisconnected={() => navigate({ name: "wizard" })} />
            )}
            {route.name === "event" && <EventDetail eventId={route.eventId} onTrace={openTree} onOpenCase={openCase} />}
            {route.name === "tree" && (
              <ProcessTree eventId={route.eventId} initialProcessId={route.processId} onOpenEvent={openEvent} onOpenCase={openCase} />
            )}
          </>
        )}
      </main>
    </div>
  );
}
