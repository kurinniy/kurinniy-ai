import { useEffect, useMemo, useState } from "react";

import type {
  AuthResponse,
  BootstrapResponse,
  DashboardPayload,
  MealEntryDetail,
  RecognitionDetail,
} from "./types";

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

type TabKey = "today" | "history" | "recognitions" | "more";

type MealDetailState =
  | { kind: "idle" }
  | { kind: "loading"; entryId: string }
  | { kind: "ready"; entryId: string; detail: MealEntryDetail }
  | { kind: "error"; entryId: string; message: string };

type RecognitionDetailState =
  | { kind: "idle" }
  | { kind: "loading"; draftId: string }
  | { kind: "ready"; draftId: string; detail: RecognitionDetail }
  | { kind: "error"; draftId: string; message: string };

const SESSION_KEY = "ai_me_web_session";

export function App() {
  const [state, setState] = useState<AppState>({ kind: "loading", label: "Запуск Mini App" });
  const [activeTab, setActiveTab] = useState<TabKey>("today");
  const [mealDetail, setMealDetail] = useState<MealDetailState>({ kind: "idle" });
  const [recognitionDetail, setRecognitionDetail] = useState<RecognitionDetailState>({ kind: "idle" });

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
    return (
      <Shell>
        <StatusCard title="ai-me" text={state.label} />
      </Shell>
    );
  }

  if (state.kind === "error") {
    return (
      <Shell>
        <Hero firstName={firstName} />
        <StatusCard title={state.title} text={state.message} tone="danger" />
      </Shell>
    );
  }

  const { auth, dashboard } = state;

  const openMealDetail = async (entryId: string) => {
    setMealDetail({ kind: "loading", entryId });
    setActiveTab("history");
    try {
      const detail = await fetchMealEntryDetail(auth.token, entryId);
      setMealDetail({ kind: "ready", entryId, detail });
    } catch (error) {
      setMealDetail({
        kind: "error",
        entryId,
        message: error instanceof Error ? error.message : "Не удалось открыть запись.",
      });
    }
  };

  const openRecognitionDetail = async (draftId: string) => {
    setRecognitionDetail({ kind: "loading", draftId });
    setActiveTab("recognitions");
    try {
      const detail = await fetchRecognitionDetail(auth.token, draftId);
      setRecognitionDetail({ kind: "ready", draftId, detail });
    } catch (error) {
      setRecognitionDetail({
        kind: "error",
        draftId,
        message: error instanceof Error ? error.message : "Не удалось открыть распознавание.",
      });
    }
  };

  return (
    <Shell>
      <Hero firstName={firstName} />
      <TabBar activeTab={activeTab} onSelect={setActiveTab} />

      {activeTab === "today" ? <TodayView dashboard={dashboard} /> : null}
      {activeTab === "history" ? <HistoryView dashboard={dashboard} detailState={mealDetail} onOpenDetail={openMealDetail} /> : null}
      {activeTab === "recognitions" ? (
        <RecognitionView dashboard={dashboard} detailState={recognitionDetail} onOpenDetail={openRecognitionDetail} />
      ) : null}
      {activeTab === "more" ? <MoreView dashboard={dashboard} /> : null}

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

  const bootstrap = await postJson<BootstrapResponse>("/api/webapp/bootstrap", {
    init_data: initData,
  });
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: bootstrap.token, expires_in: bootstrap.expires_in, user: bootstrap.user }));
  return {
    auth: {
      token: bootstrap.token,
      expires_in: bootstrap.expires_in,
      user: bootstrap.user,
    },
    dashboard: bootstrap.dashboard,
  };
}

async function fetchDashboard(token: string): Promise<DashboardPayload> {
  return getJson<DashboardPayload>("/api/dashboard", token);
}

async function fetchMealEntryDetail(token: string, entryId: string): Promise<MealEntryDetail> {
  return getJson<MealEntryDetail>(`/api/history/meals/${encodeURIComponent(entryId)}`, token);
}

async function fetchRecognitionDetail(token: string, draftId: string): Promise<RecognitionDetail> {
  return getJson<RecognitionDetail>(`/api/history/recognitions/${encodeURIComponent(draftId)}`, token);
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await safeReadJson(response);
    throw new Error(mapApiError(error?.detail));
  }
  return (await response.json()) as T;
}

async function getJson<T>(url: string, token: string): Promise<T> {
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    const error = await safeReadJson(response);
    throw new Error(mapApiError(error?.detail));
  }
  return (await response.json()) as T;
}

async function safeReadJson(response: Response): Promise<Record<string, unknown> | null> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function mapApiError(detail: unknown): string {
  if (detail === "registration_required") {
    return "Аккаунт не найден. Откройте бота в Telegram и начните работу через /start.";
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
      <p className="hero__subtitle">
        Привет, {firstName}. Здесь удобно смотреть архив приёмов пищи и распознаваний, а чат остаётся для быстрых действий.
      </p>
    </section>
  );
}

function TabBar({ activeTab, onSelect }: { activeTab: TabKey; onSelect: (tab: TabKey) => void }) {
  const items: Array<{ key: TabKey; label: string }> = [
    { key: "today", label: "Сегодня" },
    { key: "history", label: "История" },
    { key: "recognitions", label: "Распознавания" },
    { key: "more", label: "Еще" },
  ];
  return (
    <nav className="tab-bar">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          className={`tab-bar__button${activeTab === item.key ? " tab-bar__button--active" : ""}`}
          onClick={() => onSelect(item.key)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}

function TodayView({ dashboard }: { dashboard: DashboardPayload }) {
  return (
    <>
      <Section title="Сегодня">
        <MetricGrid
          items={[
            { label: "Калории", value: formatInteger(dashboard.summary.calories) },
            { label: "Белок", value: `${formatDecimal(dashboard.summary.protein_g)} г` },
            { label: "Вода", value: `${formatLiters(dashboard.summary.water_ml)} л` },
            { label: "Приемы пищи", value: String(dashboard.summary.meals_count) },
          ]}
        />
      </Section>

      <Section title="Приемы пищи за сегодня">
        {dashboard.summary.meals.length === 0 ? (
          <EmptyCard title="Пока пусто" text="Сегодня еще нет сохраненных приемов пищи." />
        ) : (
          <div className="stack">
            {dashboard.summary.meals.map((meal) => (
              <div className="card" key={meal.entry_id}>
                <div className="card__headline">
                  <span>{meal.title}</span>
                  <span className="card__muted">{formatTime(meal.occurred_at)}</span>
                </div>
                <div className="card__text">
                  {formatInteger(meal.calories)} ккал · Б {formatDecimal(meal.protein_g)} / Ж {formatDecimal(meal.fat_g)} / У{" "}
                  {formatDecimal(meal.carbs_g)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Шаги за вчера">
        <StepCard dashboard={dashboard} />
      </Section>
    </>
  );
}

function HistoryView({
  dashboard,
  detailState,
  onOpenDetail,
}: {
  dashboard: DashboardPayload;
  detailState: MealDetailState;
  onOpenDetail: (entryId: string) => void;
}) {
  return (
    <>
      <Section title="История">
        <StatusCard
          title="Архив приемов пищи"
          text="Здесь можно открыть сохраненные записи по дням. Полное редактирование появится следующим этапом."
        />
      </Section>

      {detailState.kind !== "idle" ? (
        <Section title="Карточка записи">
          <MealDetailCard detailState={detailState} />
        </Section>
      ) : null}

      <Section title="Дни">
        {dashboard.history.days.length === 0 ? (
          <EmptyCard title="История пока пустая" text="Пока нет сохраненных приемов пищи. Отправьте первое фото еды в чат." />
        ) : (
          <div className="stack">
            {dashboard.history.days.map((day) => (
              <div className="card" key={day.date}>
                <div className="card__title">{formatHistoryDate(day.date)}</div>
                <div className="list">
                  {day.entries.map((entry) => (
                    <button className="list-button" key={entry.entry_id} type="button" onClick={() => onOpenDetail(entry.entry_id)}>
                      <div className="list-button__main">
                        <span className="list-button__title">{entry.title}</span>
                        <span className="badge badge--neutral">Сохранено</span>
                      </div>
                      <div className="list-button__meta">
                        <span>{formatTime(entry.occurred_at)}</span>
                        <span>{formatInteger(entry.calories)} ккал</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        {dashboard.history.has_more ? <Hint text="Пока показываем последние записи. Дальше архив будет расширен." /> : null}
      </Section>
    </>
  );
}

function RecognitionView({
  dashboard,
  detailState,
  onOpenDetail,
}: {
  dashboard: DashboardPayload;
  detailState: RecognitionDetailState;
  onOpenDetail: (draftId: string) => void;
}) {
  return (
    <>
      <Section title="Распознавания">
        <StatusCard
          title="Лента распознаваний"
          text="Здесь видно, что уже сохранено, что отклонено и какие распознавания еще ждут решения."
        />
      </Section>

      {detailState.kind !== "idle" ? (
        <Section title="Карточка распознавания">
          <RecognitionDetailCard detailState={detailState} />
        </Section>
      ) : null}

      <Section title="Последние распознавания">
        {dashboard.recognitions.items.length === 0 ? (
          <EmptyCard title="Распознаваний пока нет" text="Пока нет распознаваний. Просто отправьте фото еды в чат." />
        ) : (
          <div className="stack">
            {dashboard.recognitions.items.map((item) => (
              <button className="card card--button" key={item.draft_id} type="button" onClick={() => onOpenDetail(item.draft_id)}>
                <div className="card__headline">
                  <span>{item.title}</span>
                  <span className={`badge ${recognitionBadgeClass(item.status)}`}>{item.status_label}</span>
                </div>
                <div className="card__text">{item.summary || "Краткое описание пока недоступно."}</div>
                <div className="chip-row">
                  <span className="chip">{formatTime(item.occurred_at)}</span>
                  <span className="chip">{formatInteger(item.calories)} ккал</span>
                  {item.is_water_only ? <span className="chip">Вода</span> : null}
                </div>
              </button>
            ))}
          </div>
        )}
        {dashboard.recognitions.has_more ? <Hint text="Пока показываем последние распознавания. История будет расширяться дальше." /> : null}
      </Section>
    </>
  );
}

function MoreView({ dashboard }: { dashboard: DashboardPayload }) {
  return (
    <>
      <Section title="Профиль и еще">
        <div className="stack">
          <div className="card">
            <div className="card__headline">
              <span>{dashboard.user.first_name || "Пользователь"}</span>
              <span className="badge badge--neutral">{dashboard.user.status}</span>
            </div>
            <div className="card__text">
              Telegram ID: {dashboard.user.telegram_user_id}
              <br />
              В приложении сейчас доступны история, распознавания и просмотр карточек записей.
            </div>
          </div>
          <DecisionList dashboard={dashboard} />
        </div>
      </Section>
    </>
  );
}

function MealDetailCard({ detailState }: { detailState: MealDetailState }) {
  if (detailState.kind === "loading") {
    return <StatusCard title="Открываю запись" text="Загружаю карточку приема пищи." />;
  }
  if (detailState.kind === "error") {
    return <StatusCard title="Не удалось открыть запись" text={detailState.message} tone="danger" />;
  }
  const detail = detailState.detail;
  return (
    <div className="card detail-card">
      {detail.photo_data_url ? <img className="detail-card__image" src={detail.photo_data_url} alt={detail.title} /> : null}
      <div className="card__headline">
        <span>{detail.title}</span>
        <span className="badge badge--neutral">{detail.status_label}</span>
      </div>
      <div className="chip-row">
        <span className="chip">{formatDateTime(detail.occurred_at)}</span>
        <span className="chip">{formatInteger(detail.calories)} ккал</span>
      </div>
      {detail.summary ? <div className="card__text">{detail.summary}</div> : null}
      <MetricGrid
        compact
        items={[
          { label: "Белок", value: `${formatDecimal(detail.protein_g)} г` },
          { label: "Жиры", value: `${formatDecimal(detail.fat_g)} г` },
          { label: "Углеводы", value: `${formatDecimal(detail.carbs_g)} г` },
          { label: "Вода", value: `${formatLiters(detail.water_ml)} л` },
        ]}
      />
      <div className="detail-card__footer">Редактирование скоро появится в Mini App.</div>
    </div>
  );
}

function RecognitionDetailCard({ detailState }: { detailState: RecognitionDetailState }) {
  if (detailState.kind === "loading") {
    return <StatusCard title="Открываю распознавание" text="Загружаю карточку распознавания." />;
  }
  if (detailState.kind === "error") {
    return <StatusCard title="Не удалось открыть распознавание" text={detailState.message} tone="danger" />;
  }
  const detail = detailState.detail;
  return (
    <div className="card detail-card">
      {detail.photo_data_url ? <img className="detail-card__image" src={detail.photo_data_url} alt={detail.title} /> : null}
      <div className="card__headline">
        <span>{detail.title}</span>
        <span className={`badge ${recognitionBadgeClass(detail.status)}`}>{detail.status_label}</span>
      </div>
      <div className="chip-row">
        <span className="chip">{formatDateTime(detail.occurred_at)}</span>
        <span className="chip">{formatInteger(detail.calories)} ккал</span>
        <span className="chip">Уверенность {Math.round(detail.confidence * 100)}%</span>
      </div>
      {detail.summary ? <div className="card__text">{detail.summary}</div> : null}
      <MetricGrid
        compact
        items={[
          { label: "Белок", value: `${formatDecimal(detail.protein_g)} г` },
          { label: "Жиры", value: `${formatDecimal(detail.fat_g)} г` },
          { label: "Углеводы", value: `${formatDecimal(detail.carbs_g)} г` },
          { label: "Вода", value: `${formatLiters(detail.water_ml)} л` },
        ]}
      />
      <div className="detail-card__footer">Редактирование и удаление появятся следующим этапом.</div>
    </div>
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

function EmptyCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="card card--empty">
      <div className="card__title">{title}</div>
      <div className="card__text">{text}</div>
    </div>
  );
}

function Hint({ text }: { text: string }) {
  return <div className="hint">{text}</div>;
}

function MetricGrid({
  items,
  compact = false,
}: {
  items: Array<{ label: string; value: string }>;
  compact?: boolean;
}) {
  return (
    <div className={`metric-grid${compact ? " metric-grid--compact" : ""}`}>
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
        <span>{formatInteger(stepProgress.steps)} шагов</span>
        <span className="card__muted">цель {formatInteger(stepProgress.target_steps)}</span>
      </div>
      <div className="card__text">{stepProgress.comment}</div>
      <div className="chip-row">
        <span className="chip">Средняя 30 дней: {stepProgress.average_steps_30d ?? "нет данных"}</span>
        <span className="chip">Дней с данными: {stepProgress.days_with_data_30d}</span>
      </div>
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

function formatInteger(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatDecimal(value: number): string {
  return value.toFixed(1);
}

function formatLiters(valueMl: number): string {
  return (valueMl / 1000).toFixed(1);
}

function formatTime(raw: string): string {
  return new Date(raw).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(raw: string): string {
  return new Date(raw).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatHistoryDate(raw: string): string {
  const value = new Date(`${raw}T00:00:00`);
  return value.toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function recognitionBadgeClass(status: string): string {
  if (status === "confirmed") {
    return "badge--success";
  }
  if (status === "rejected") {
    return "badge--danger";
  }
  return "badge--warning";
}
