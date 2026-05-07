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

export type BootstrapResponse = AuthResponse & {
  dashboard: DashboardPayload;
};
