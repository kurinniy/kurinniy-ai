import { useEffect, useMemo, useState } from "react";

import type { AuthResponse, BootstrapResponse, DashboardPayload } from "./types";

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        ready: () => void;
        expand: () => void;
        initData: string;
        initDataUnsafe?: {
          user?: {
            first_name?: string;
          };
        };
      };
    };
  }
}

type AppState =
  | { kind: "loading"; label: string }
  | { kind: "error"; title: string; message: string }
  | { kind: "ready"; auth: AuthResponse; dashboard: DashboardPayload };

const SESSION_KEY = "ai_me_web_session";

export function App() {
  const [state, setState] = useState<AppState>({ kind: "loading", label: "Запуск Mini App" });

  useEffect(() => {
    const webApp = window.Telegram?.WebApp;
    webApp?.ready();
    webApp?.expand();

    const run = async () => {
      const initData = webApp?.initData ?? "";
      if (!initData) {
        setState({
          kind: "error",
          title: "Mini App не авторизован",
          message: "Открой приложение из Telegram, чтобы получить доступ к данным.",
        });
        return;
      }

      try {
        const result = await loadApplication(initData);
        setState({ kind: "ready", auth: result.auth, dashboard: result.dashboard });
      } catch (error) {
        setState({
          kind: "error",
          title: "Не удалось открыть приложение",
          message: error instanceof Error ? error.message : "Неизвестная ошибка",
        });
      }
    };

    void run();
  }, []);

  const firstName = useMemo(() => {
    if (state.kind !== "ready") {
      return window.Telegram?.WebApp?.initDataUnsafe?.user?.first_name ?? "Пользователь";
    }
    return state.auth.user.first_name || "Пользователь";
  }, [state]);

  if (state.kind === "loading") {
    return <Shell><StatusCard title="ai-me" text={state.label} /></Shell>;
  }

  if (state.kind === "error") {
    return (
      <Shell>
        <Hero firstName={firstName} />
        <StatusCard title={state.title} text={state.message} tone="danger" />
      </Shell>
    );
  }

  const { dashboard } = state;
  return (
    <Shell>
      <Hero firstName={firstName} />

      <Section title="Сегодня">
        <MetricGrid
          items={[
            { label: "Калории", value: String(dashboard.summary.calories) },
            { label: "Белок", value: `${dashboard.summary.protein_g.toFixed(1)} г` },
            { label: "Вода", value: `${dashboard.summary.water_ml} мл` },
            { label: "Шаги", value: String(dashboard.summary.steps) },
          ]}
        />
      </Section>

      <Section title="Шаги за вчера">
        <StepCard dashboard={dashboard} />
      </Section>

      <Section title="Еда">
        <MealList dashboard={dashboard} />
      </Section>

      <Section title="Открытые решения">
        <DecisionList dashboard={dashboard} />
      </Section>

      <Footer version={dashboard.version.app_version} releaseDate={dashboard.version.release_date} />
    </Shell>
  );
}

async function loadApplication(initData: string): Promise<{ auth: AuthResponse; dashboard: DashboardPayload }> {
  const cached = sessionStorage.getItem(SESSION_KEY);
  if (cached) {
    const parsed = JSON.parse(cached) as AuthResponse;
    try {
      const dashboard = await fetchDashboard(parsed.token);
      return { auth: parsed, dashboard };
    } catch {
      sessionStorage.removeItem(SESSION_KEY);
    }
  }

  const response = await fetch("/api/webapp/bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
  });
  if (!response.ok) {
    const payload = await safeJson(response);
    throw new Error(mapApiError(payload?.detail));
  }
  const bootstrap = (await response.json()) as BootstrapResponse;
  const auth: AuthResponse = {
    token: bootstrap.token,
    expires_in: bootstrap.expires_in,
    user: bootstrap.user,
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(auth));
  return { auth, dashboard: bootstrap.dashboard };
}

async function fetchDashboard(token: string): Promise<DashboardPayload> {
  const response = await fetch("/api/dashboard", {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    const payload = await safeJson(response);
    throw new Error(mapApiError(payload?.detail));
  }
  return (await response.json()) as DashboardPayload;
}

async function safeJson(response: Response): Promise<Record<string, unknown> | null> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function mapApiError(detail: unknown): string {
  if (detail === "registration_required") {
    return "Аккаунт ещё не подключён. Сначала активируйте бота по инвайту в чате.";
  }
  if (detail === "mini_app_admin_required") {
    return "Mini App доступен только администратору.";
  }
  if (detail === "blocked") {
    return "Доступ к приложению заблокирован.";
  }
  if (typeof detail === "string" && detail.length > 0) {
    return detail;
  }
  return "Не удалось загрузить данные.";
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="shell">{children}</main>;
}

function Hero({ firstName }: { firstName: string }) {
  return (
    <section className="hero">
      <div className="hero__eyebrow">Telegram Mini App</div>
      <h1 className="hero__title">ai-me</h1>
      <p className="hero__subtitle">Привет, {firstName}. Здесь сводка по здоровью, активности и интеграциям без чата-команд.</p>
    </section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="section">
      <div className="section__title">{title}</div>
      {children}
    </section>
  );
}

function StatusCard({ title, text, tone = "default" }: { title: string; text: string; tone?: "default" | "danger" }) {
  return (
    <div className={`card card--${tone}`}>
      <div className="card__title">{title}</div>
      <div className="card__text">{text}</div>
    </div>
  );
}

function MetricGrid({ items }: { items: Array<{ label: string; value: string }> }) {
  return (
    <div className="metric-grid">
      {items.map((item) => (
        <div className="metric-tile" key={item.label}>
          <div className="metric-tile__value">{item.value}</div>
          <div className="metric-tile__label">{item.label}</div>
        </div>
      ))}
    </div>
  );
}

function StepCard({ dashboard }: { dashboard: DashboardPayload }) {
  const stepProgress = dashboard.summary.step_progress;
  return (
    <div className="card">
      <div className="card__headline">
        <span>{stepProgress.steps} шагов</span>
        <span className="card__muted">цель {stepProgress.target_steps}</span>
      </div>
      <div className="card__text">{stepProgress.comment}</div>
      <div className="chip-row">
        <span className="chip">Средняя 30 дней: {stepProgress.average_steps_30d ?? "нет данных"}</span>
        <span className="chip">Дней с данными: {stepProgress.days_with_data_30d}</span>
      </div>
    </div>
  );
}

function MealList({ dashboard }: { dashboard: DashboardPayload }) {
  if (dashboard.summary.meals.length === 0) {
    return <StatusCard title="Еда" text="За выбранный день нет подтверждённых приёмов пищи." />;
  }
  return (
    <div className="stack">
      {dashboard.summary.meals.map((meal) => (
        <div className="card" key={meal.entry_id}>
          <div className="card__headline">
            <span>{meal.title}</span>
            <span className="card__muted">{new Date(meal.occurred_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>
          </div>
          <div className="card__text">
            {meal.calories} ккал · Б {meal.protein_g.toFixed(1)} / Ж {meal.fat_g.toFixed(1)} / У {meal.carbs_g.toFixed(1)}
          </div>
        </div>
      ))}
    </div>
  );
}

function DecisionList({ dashboard }: { dashboard: DashboardPayload }) {
  if (dashboard.decisions.length === 0) {
    return <StatusCard title="Открытые решения" text="На сегодня открытых решений нет." />;
  }
  return (
    <div className="stack">
      {dashboard.decisions.map((decision) => (
        <div className="card" key={decision.decision_id}>
          <div className="card__headline">
            <span>{decision.title}</span>
            <span className="badge">{decision.kind}</span>
          </div>
          <div className="card__text">{decision.rationale}</div>
        </div>
      ))}
    </div>
  );
}

function Footer({ version, releaseDate }: { version: string; releaseDate: string }) {
  return (
    <footer className="footer">
      <span>Версия {version}</span>
      <span>Релиз {releaseDate}</span>
    </footer>
  );
}
