export type DashboardPayload = {
  user: {
    user_id: number;
    telegram_user_id: number;
    username: string;
    first_name: string;
    is_admin: boolean;
    is_admin_account: boolean;
    admin_mode_enabled: boolean;
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
    goals: {
      water_ml: number;
      protein_g: number;
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
  };
  history: {
    days: Array<{
      date: string;
      entries: MealHistoryListItem[];
    }>;
    has_more: boolean;
  };
  recognitions: {
    items: RecognitionListItem[];
    has_more: boolean;
  };
  analytics: {
    window_days: number;
    points: AnalyticsPoint[];
    logging_days: number;
    logging_frequency_pct: number;
    current_streak: number;
    longest_streak: number;
    average_calories: number;
    average_protein_g: number;
    average_water_ml: number;
  };
  profile: ProfilePayload;
  decisions: Array<{
    decision_id: string;
    kind: string;
    title: string;
    rationale: string;
    status: string;
    context_date: string;
  }>;
};

export type MealHistoryListItem = {
  entry_id: string;
  occurred_at: string;
  created_at: string;
  title: string;
  calories: number;
  status: string;
};

export type RecognitionListItem = {
  draft_id: string;
  created_at: string;
  occurred_at: string;
  title: string;
  summary: string;
  calories: number;
  status: string;
  status_label: string;
  is_water_only: boolean;
};

export type MealEntryDetail = {
  entry_id: string;
  occurred_at: string;
  created_at: string;
  title: string;
  summary: string;
  calories: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  water_ml: number;
  status: string;
  status_label: string;
  photo_data_url: string | null;
};

export type RecognitionDetail = {
  draft_id: string;
  created_at: string;
  occurred_at: string;
  title: string;
  summary: string;
  calories: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  water_ml: number;
  confidence: number;
  status: string;
  status_label: string;
  is_water_only: boolean;
  photo_data_url: string | null;
};

export type AnalyticsPoint = {
  date: string;
  calories: number;
  protein_g: number;
  water_ml: number;
  meals_count: number;
  has_logging: boolean;
};

export type ProfilePayload = {
  about: {
    sex: string | null;
    sex_label: string;
    age_years: number | null;
    height_cm: number | null;
    profile_weight_kg: number | null;
    goal: string | null;
    goal_label: string;
  };
  goals: {
    target_water_ml: number;
    target_protein_g: number;
    target_calories_min: number | null;
    target_calories_max: number | null;
  };
  reminders: {
    enabled: boolean;
    meal_logging: boolean;
    water: boolean;
    evening_summary: boolean;
  };
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
    is_admin_account: boolean;
    admin_mode_enabled: boolean;
    status: string;
  };
};

export type BootstrapResponse = AuthResponse & {
  dashboard: DashboardPayload;
};
