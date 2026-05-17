import { useEffect, useMemo, useState } from "react";

import type {
  AnalyticsPoint,
  AuthResponse,
  BootstrapResponse,
  DashboardPayload,
  MealEntryDetail,
  ProfilePayload,
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

type TabKey = "today" | "history" | "analytics" | "profile";

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
const MIN_LOADING_SCREEN_MS = 900;

export function App() {
  const [state, setState] = useState<AppState>({ kind: "loading", label: "Загрузка приложения" });
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

      const startedAt = Date.now();
      try {
        const result = await loadApplication(initData);
        await ensureMinimumLoadingTime(startedAt);
        setState({ kind: "ready", auth: result.auth, dashboard: result.dashboard });
      } catch (error) {
        await ensureMinimumLoadingTime(startedAt);
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
        <LoadingScreen label={state.label} />
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

  const replaceProfile = (profile: ProfilePayload) => {
    setState((current) => {
      if (current.kind !== "ready") {
        return current;
      }
      return {
        ...current,
        dashboard: {
          ...current.dashboard,
          profile,
        },
      };
    });
  };

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
    setActiveTab("history");
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
      {activeTab === "history" ? (
        <HistoryView
          dashboard={dashboard}
          mealDetailState={mealDetail}
          recognitionDetailState={recognitionDetail}
          onOpenMealDetail={openMealDetail}
          onOpenRecognitionDetail={openRecognitionDetail}
        />
      ) : null}
      {activeTab === "analytics" ? <AnalyticsView dashboard={dashboard} /> : null}
      {activeTab === "profile" ? (
        <ProfileView
          token={auth.token}
          dashboard={dashboard}
          onProfileUpdate={replaceProfile}
        />
      ) : null}

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
  sessionStorage.setItem(
    SESSION_KEY,
    JSON.stringify({ token: bootstrap.token, expires_in: bootstrap.expires_in, user: bootstrap.user }),
  );
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

async function updateProfileAbout(token: string, payload: Record<string, unknown>): Promise<ProfilePayload> {
  return patchJson<ProfilePayload>("/api/profile/about", token, payload);
}

async function updateProfileGoals(token: string, payload: Record<string, unknown>): Promise<ProfilePayload> {
  return patchJson<ProfilePayload>("/api/profile/goals", token, payload);
}

async function resetProfileGoals(token: string): Promise<ProfilePayload> {
  return postAuthJson<ProfilePayload>("/api/profile/goals/reset", token, {});
}

async function updateProfileReminders(token: string, payload: Record<string, unknown>): Promise<ProfilePayload> {
  return patchJson<ProfilePayload>("/api/profile/reminders", token, payload);
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

async function postAuthJson<T>(url: string, token: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
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

async function patchJson<T>(url: string, token: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
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

async function ensureMinimumLoadingTime(startedAt: number): Promise<void> {
  const elapsed = Date.now() - startedAt;
  const remaining = MIN_LOADING_SCREEN_MS - elapsed;
  if (remaining > 0) {
    await new Promise((resolve) => window.setTimeout(resolve, remaining));
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
      <h1 className="hero__title">Что я ем!</h1>
      <p className="hero__subtitle">
        Привет, {firstName}. Здесь удобно смотреть историю, аналитику и настраивать профиль, а чат остаётся для быстрых действий.
      </p>
    </section>
  );
}

function TabBar({ activeTab, onSelect }: { activeTab: TabKey; onSelect: (tab: TabKey) => void }) {
  const items: Array<{ key: TabKey; label: string }> = [
    { key: "today", label: "Сегодня" },
    { key: "history", label: "История" },
    { key: "analytics", label: "Аналитика" },
    { key: "profile", label: "Профиль" },
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
            { label: "Приемы пищи", value: formatInteger(dashboard.summary.meals_count) },
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
    </>
  );
}

function HistoryView({
  dashboard,
  mealDetailState,
  recognitionDetailState,
  onOpenMealDetail,
  onOpenRecognitionDetail,
}: {
  dashboard: DashboardPayload;
  mealDetailState: MealDetailState;
  recognitionDetailState: RecognitionDetailState;
  onOpenMealDetail: (entryId: string) => void;
  onOpenRecognitionDetail: (draftId: string) => void;
}) {
  return (
    <>
      <Section title="История">
        <StatusCard title="Архив приемов пищи" text="Здесь удобно просматривать сохраненные записи и историю распознаваний." />
      </Section>

      {mealDetailState.kind !== "idle" ? (
        <Section title="Карточка записи">
          <MealDetailCard detailState={mealDetailState} />
        </Section>
      ) : null}

      {recognitionDetailState.kind !== "idle" ? (
        <Section title="Карточка распознавания">
          <RecognitionDetailCard detailState={recognitionDetailState} />
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
                    <button className="list-button" key={entry.entry_id} type="button" onClick={() => onOpenMealDetail(entry.entry_id)}>
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
      </Section>

      <Section title="Распознавания">
        {dashboard.recognitions.items.length === 0 ? (
          <EmptyCard title="Распознаваний пока нет" text="Пока нет распознаваний. Просто отправьте фото еды в чат." />
        ) : (
          <div className="stack">
            {dashboard.recognitions.items.map((item) => (
              <button className="card card--button" key={item.draft_id} type="button" onClick={() => onOpenRecognitionDetail(item.draft_id)}>
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
      </Section>
    </>
  );
}

function AnalyticsView({ dashboard }: { dashboard: DashboardPayload }) {
  const analytics = dashboard.analytics;

  return (
    <>
      <Section title="Аналитика">
        <MetricGrid
          items={[
            { label: "Дней с логированием", value: String(analytics.logging_days) },
            { label: "Частота", value: `${formatPercent(analytics.logging_frequency_pct)}` },
            { label: "Текущий streak", value: `${analytics.current_streak} дн` },
            { label: "Лучший streak", value: `${analytics.longest_streak} дн` },
          ]}
        />
      </Section>

      <Section title="Средние за окно">
        <MetricGrid
          items={[
            { label: "Калории", value: `${formatDecimal(analytics.average_calories)} ккал` },
            { label: "Белок", value: `${formatDecimal(analytics.average_protein_g)} г` },
            { label: "Вода", value: `${formatLiters(analytics.average_water_ml)} л` },
            { label: "Период", value: `${analytics.window_days} дней` },
          ]}
        />
      </Section>

      <Section title="Калории по дням">
        <AnalyticsBars
          points={analytics.points}
          getValue={(point) => point.calories}
          renderValue={(point) => `${formatInteger(point.calories)} ккал`}
        />
      </Section>

      <Section title="Белок по дням">
        <AnalyticsBars
          points={analytics.points}
          getValue={(point) => point.protein_g}
          renderValue={(point) => `${formatDecimal(point.protein_g)} г`}
        />
      </Section>

      <Section title="Вода по дням">
        <AnalyticsBars
          points={analytics.points}
          getValue={(point) => point.water_ml}
          renderValue={(point) => `${formatLiters(point.water_ml)} л`}
        />
      </Section>
    </>
  );
}

function ProfileView({
  token,
  dashboard,
  onProfileUpdate,
}: {
  token: string;
  dashboard: DashboardPayload;
  onProfileUpdate: (profile: ProfilePayload) => void;
}) {
  const [draft, setDraft] = useState<ProfilePayload>(dashboard.profile);
  const [status, setStatus] = useState<{ tone: "default" | "danger"; text: string } | null>(null);

  useEffect(() => {
    setDraft(dashboard.profile);
  }, [dashboard.profile]);

  const saveAbout = async () => {
    setStatus({ tone: "default", text: "Сохраняю изменения..." });
    try {
      const profile = await updateProfileAbout(token, draft.about);
      setDraft(profile);
      onProfileUpdate(profile);
      setStatus({ tone: "default", text: "Параметры сохранены." });
    } catch (error) {
      setStatus({ tone: "danger", text: error instanceof Error ? error.message : "Не удалось сохранить профиль." });
    }
  };

  const saveGoals = async () => {
    setStatus({ tone: "default", text: "Сохраняю цели..." });
    try {
      const profile = await updateProfileGoals(token, draft.goals);
      setDraft(profile);
      onProfileUpdate(profile);
      setStatus({ tone: "default", text: "Цели сохранены." });
    } catch (error) {
      setStatus({ tone: "danger", text: error instanceof Error ? error.message : "Не удалось сохранить цели." });
    }
  };

  const resetGoalsAction = async () => {
    setStatus({ tone: "default", text: "Сбрасываю цели..." });
    try {
      const profile = await resetProfileGoals(token);
      setDraft(profile);
      onProfileUpdate(profile);
      setStatus({ tone: "default", text: "Цели сброшены к рекомендованным." });
    } catch (error) {
      setStatus({ tone: "danger", text: error instanceof Error ? error.message : "Не удалось сбросить цели." });
    }
  };

  const saveReminders = async () => {
    setStatus({ tone: "default", text: "Сохраняю напоминания..." });
    try {
      const profile = await updateProfileReminders(token, draft.reminders);
      setDraft(profile);
      onProfileUpdate(profile);
      setStatus({ tone: "default", text: "Настройки напоминаний сохранены." });
    } catch (error) {
      setStatus({ tone: "danger", text: error instanceof Error ? error.message : "Не удалось сохранить напоминания." });
    }
  };

  return (
    <>
      <Section title="Профиль">
        <StatusCard
          title="Настройки пользователя"
          text="Здесь можно обновить личные параметры, цели и напоминания. Чат остаётся быстрым местом для действий, а Mini App — местом для настроек."
        />
      </Section>

      {status ? <StatusCard title="Статус" text={status.text} tone={status.tone} /> : null}

      <Section title="Обо мне">
        <div className="card form-card">
          <div className="form-grid">
            <label className="field">
              <span className="field__label">Пол</span>
              <select
                className="field__control"
                value={draft.about.sex ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    about: { ...current.about, sex: event.target.value || null },
                  }))
                }
              >
                <option value="">Не указан</option>
                <option value="male">Мужчина</option>
                <option value="female">Женщина</option>
              </select>
            </label>
            <label className="field">
              <span className="field__label">Возраст</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.about.age_years ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    about: { ...current.about, age_years: parseNullableInt(event.target.value) },
                  }))
                }
              />
            </label>
            <label className="field">
              <span className="field__label">Рост</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.about.height_cm ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    about: { ...current.about, height_cm: parseNullableInt(event.target.value) },
                  }))
                }
              />
            </label>
            <label className="field">
              <span className="field__label">Вес</span>
              <input
                className="field__control"
                type="number"
                inputMode="decimal"
                step="0.1"
                value={draft.about.profile_weight_kg ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    about: { ...current.about, profile_weight_kg: parseNullableFloat(event.target.value) },
                  }))
                }
              />
            </label>
            <label className="field field--full">
              <span className="field__label">Цель</span>
              <select
                className="field__control"
                value={draft.about.goal ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    about: { ...current.about, goal: event.target.value || null },
                  }))
                }
              >
                <option value="">Не указана</option>
                <option value="maintenance">Поддержание</option>
                <option value="weight_loss">Похудение</option>
                <option value="mass_gain">Набор массы</option>
              </select>
            </label>
          </div>
          <div className="action-row">
            <button className="action-button" type="button" onClick={saveAbout}>
              Сохранить параметры
            </button>
          </div>
        </div>
      </Section>

      <Section title="Цели">
        <div className="card form-card">
          <div className="form-grid">
            <label className="field">
              <span className="field__label">Вода, мл</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.goals.target_water_ml}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    goals: { ...current.goals, target_water_ml: parseNullableInt(event.target.value) ?? 0 },
                  }))
                }
              />
            </label>
            <label className="field">
              <span className="field__label">Белок, г</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.goals.target_protein_g}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    goals: { ...current.goals, target_protein_g: parseNullableInt(event.target.value) ?? 0 },
                  }))
                }
              />
            </label>
            <label className="field">
              <span className="field__label">Калории от</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.goals.target_calories_min ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    goals: { ...current.goals, target_calories_min: parseNullableInt(event.target.value) },
                  }))
                }
              />
            </label>
            <label className="field">
              <span className="field__label">Калории до</span>
              <input
                className="field__control"
                type="number"
                inputMode="numeric"
                value={draft.goals.target_calories_max ?? ""}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    goals: { ...current.goals, target_calories_max: parseNullableInt(event.target.value) },
                  }))
                }
              />
            </label>
          </div>
          <div className="action-row">
            <button className="action-button" type="button" onClick={saveGoals}>
              Сохранить цели
            </button>
            <button className="action-button action-button--secondary" type="button" onClick={resetGoalsAction}>
              Сбросить к рекомендованным
            </button>
          </div>
        </div>
      </Section>

      <Section title="Напоминания">
        <div className="card form-card">
          <div className="toggle-list">
            <ToggleRow
              label="Включить напоминания"
              checked={draft.reminders.enabled}
              onChange={(checked) =>
                setDraft((current) => ({
                  ...current,
                  reminders: {
                    ...current.reminders,
                    enabled: checked,
                    meal_logging: checked ? current.reminders.meal_logging : false,
                    water: checked ? current.reminders.water : false,
                    evening_summary: checked ? current.reminders.evening_summary : false,
                  },
                }))
              }
            />
            <ToggleRow
              label="Напомнить записать еду"
              checked={draft.reminders.meal_logging}
              disabled={!draft.reminders.enabled}
              onChange={(checked) =>
                setDraft((current) => ({
                  ...current,
                  reminders: { ...current.reminders, meal_logging: checked, enabled: true },
                }))
              }
            />
            <ToggleRow
              label="Напомнить про воду"
              checked={draft.reminders.water}
              disabled={!draft.reminders.enabled}
              onChange={(checked) =>
                setDraft((current) => ({
                  ...current,
                  reminders: { ...current.reminders, water: checked, enabled: true },
                }))
              }
            />
            <ToggleRow
              label="Вечерний итог дня"
              checked={draft.reminders.evening_summary}
              disabled={!draft.reminders.enabled}
              onChange={(checked) =>
                setDraft((current) => ({
                  ...current,
                  reminders: { ...current.reminders, evening_summary: checked, enabled: true },
                }))
              }
            />
          </div>
          <div className="action-row">
            <button className="action-button" type="button" onClick={saveReminders}>
              Сохранить напоминания
            </button>
          </div>
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
    </div>
  );
}

function AnalyticsBars({
  points,
  getValue,
  renderValue,
}: {
  points: AnalyticsPoint[];
  getValue: (point: AnalyticsPoint) => number;
  renderValue: (point: AnalyticsPoint) => string;
}) {
  const maxValue = Math.max(...points.map((point) => getValue(point)), 1);
  return (
    <div className="chart">
      {points.map((point) => {
        const value = getValue(point);
        const height = Math.max(8, Math.round((value / maxValue) * 100));
        return (
          <div className="chart__item" key={point.date}>
            <div className="chart__bar-wrap">
              <div className={`chart__bar${point.has_logging ? "" : " chart__bar--muted"}`} style={{ height: `${height}%` }} />
            </div>
            <div className="chart__value">{renderValue(point)}</div>
            <div className="chart__label">{formatShortDate(point.date)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ToggleRow({
  label,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`toggle-row${disabled ? " toggle-row--disabled" : ""}`}>
      <span>{label}</span>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
    </label>
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

function LoadingScreen({ label }: { label: string }) {
  return (
    <section className="loading-screen">
      <div className="loading-screen__orb" />
      <div className="loading-screen__logo">Что я ем!</div>
      <div className="loading-screen__label">{label}</div>
      <div className="loading-screen__dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </section>
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

function Footer({ version, releaseDate }: { version: string; releaseDate: string }) {
  return (
    <footer className="footer">
      <span>Версия {version}</span>
      <span>Релиз {releaseDate}</span>
    </footer>
  );
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(Math.round(value));
}

function formatDecimal(value: number): string {
  return value.toFixed(1);
}

function formatLiters(valueMl: number): string {
  return (valueMl / 1000).toFixed(1);
}

function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
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

function formatShortDate(raw: string): string {
  const value = new Date(`${raw}T00:00:00`);
  return value.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
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

function parseNullableInt(value: string): number | null {
  if (!value.trim()) {
    return null;
  }
  return Number.parseInt(value, 10);
}

function parseNullableFloat(value: string): number | null {
  if (!value.trim()) {
    return null;
  }
  return Number.parseFloat(value);
}
