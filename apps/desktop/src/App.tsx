import { useEffect, useState } from "react";
import { onEngineError, type EngineError } from "./api/client";
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
  | { name: "tree"; eventId: number };

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

const BANNER_CODES = new Set(["ELK_AUTH_FAILED", "ELK_FORBIDDEN", "ELK_TLS_ERROR", "STORAGE_QUOTA", "DB_UNAVAILABLE"]);

function initialRoute(): Route {
  const params = new URLSearchParams(window.location.search);
  const screen = params.get("screen");
  const eventId = Number(params.get("event"));
  if ((screen === "tree" || screen === "event") && eventId) return { name: screen, eventId };
  if (screen && NAV.some((n) => n.name === screen)) return { name: screen } as Route;
  return { name: "search" };
}

export function App() {
  const [route, setRoute] = useState<Route>(initialRoute);
  const [banner, setBanner] = useState<EngineError>();

  const navigate = (next: Route) => {
    const params = new URLSearchParams();
    params.set("screen", next.name);
    if ((next.name === "event" || next.name === "tree") && "eventId" in next) {
      params.set("event", String(next.eventId));
    }
    window.history.pushState(null, "", `${window.location.pathname}?${params.toString()}`);
    setRoute(next);
  };

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
  const openTree = (eventId: number) => navigate({ name: "tree", eventId });

  return (
    <div className="app">
      <nav aria-label="Điều hướng chính">
        <div className="brand">IR Desktop</div>
        {NAV.map((item) => (
          <button
            key={item.name}
            type="button"
            className={route.name === item.name ? "active" : ""}
            aria-current={route.name === item.name ? "page" : undefined}
            onClick={() => navigate({ name: item.name } as Route)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <main>
        {banner && (
          <div className="banner" role="alert">
            <strong>{banner.body.code}</strong> {banner.message}
            {banner.body.code === "ELK_AUTH_FAILED" && <button onClick={() => navigate({ name: "wizard" })}>Nhập API key mới</button>}
            <button onClick={() => setBanner(undefined)}>Đóng</button>
          </div>
        )}
        {route.name === "health" && <Health />}
        {route.name === "wizard" && <Wizard onSaved={() => navigate({ name: "sync" })} />}
        {route.name === "sync" && <SyncOverview />}
        {route.name === "search" && <Search onOpenEvent={openEvent} onTrace={openTree} />}
        {route.name === "alerts" && <Alerts onOpenEvent={openEvent} />}
        {route.name === "unresolved" && <Unresolved onOpenEvent={openEvent} />}
        {route.name === "cases" && <Cases />}
        {route.name === "settings" && <Settings onReconfigure={() => navigate({ name: "wizard" })} />}
        {route.name === "event" && <EventDetail eventId={route.eventId} onTrace={openTree} />}
        {route.name === "tree" && <ProcessTree eventId={route.eventId} onOpenEvent={openEvent} />}
      </main>
    </div>
  );
}
