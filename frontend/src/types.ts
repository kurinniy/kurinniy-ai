export type DashboardPayload = {
  user: {
    user_id: number;
    telegram_user_id: number;
    username: string;
    first_name: string;
    is_admin: boolean;
    status: string;
  };
  version: {
    app_version: string;
    release_date: string;
  };
  summary: {
    target_date: string;
    meals_count: number;
    calories: number;
    protein_g: number;
    fat_g: number;
    carbs_g: number;
    water_ml: number;
    sleep_hours: number;
    steps: number;
    activity_minutes: number;
    latest_weight_kg: number | null;
    goals: {
      water_ml: number;
      protein_g: number;
      sleep_hours: number;
      steps: number;
    };
    meals: Array<{
      entry_id: string;
      occurred_at: string;
      title: string;
      calories: number;
      protein_g: number;
      fat_g: number;
      carbs_g: number;
    }>;
    step_progress: {
      reference_date: string;
      steps: number;
      target_steps: number;
      average_steps_30d: number | null;
      days_with_data_30d: number;
      comment: string;
    };
  };
  decisions: Array<{
    decision_id: string;
    kind: string;
    title: string;
    rationale: string;
    status: string;
    context_date: string;
  }>;
  finance: {
    month_start: string;
    month_end: string;
    transaction_count: number;
    income_total: number;
    expense_total: number;
    net_total: number;
    top_expense_categories: Array<{
      category: string;
      amount: number;
      transaction_count: number;
    }>;
  };
  digest: {
    timezone_name: string;
    daily_enabled: boolean;
    daily_time: string;
    weekly_enabled: boolean;
    weekly_time: string;
    weekly_weekday: number;
  };
  drive: {
    connected: boolean;
    enabled: boolean;
    folder_id: string;
    folder_url: string;
    recent_imports: Array<{
      file_name: string;
      status: string;
      file_date: string | null;
      imported_at: string;
      activity_entries_count: number;
      error_message: string;
    }>;
  };
  drafts: Array<{
    draft_id: string;
    title: string;
    calories: number;
    confidence: number;
    occurred_at: string;
  }>;
};

export type AuthResponse = {
  token: string;
  expires_in: number;
  user: {
    user_id: number;
    telegram_user_id: number;
    first_name: string;
    username: string;
    is_admin: boolean;
    status: string;
  };
};
