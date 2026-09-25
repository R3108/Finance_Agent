export type MonthRow = { month: string; income: number; spending: number; net: number; savings_rate_pct: number | null; partial: boolean };

export type CategoryRow = {
  category: string; amount: number; transactions: number; share_pct: number | null;
  previous_month: number; six_month_avg: number; expected_to_date: number; vs_six_month_avg_pct: number | null;
};
export type Breakdown = { month: string; total: number; partial: boolean; categories: CategoryRow[] };

export type Insight = { type: string; severity: "high" | "medium" | "low" | "info"; title: string; detail: string; impact_monthly: number };

export type Health = {
  score: number; grade: string;
  components: { name: string; weight: number; score: number; detail: string }[];
  metrics: Record<string, number>;
};

export type SafeToSpend = {
  month: string; income_received: number; income_expected: number; spent_so_far: number; recurring_still_due: number;
  upcoming_charges: { merchant: string; amount: number; date: string }[];
  savings_target_pct: number; savings_target: number; safe_to_spend: number; days_left: number; per_day: number;
};

export type Account = { id: number; name: string; type: string; institution: string; balance: number };

export type Overview = {
  as_of: string;
  balances: { accounts: Account[]; net_cash: number; liquid_cash: number };
  current_month: Breakdown;
  monthly: MonthRow[];
  health: Health;
  safe_to_spend: SafeToSpend;
  subscriptions: { active_subscriptions: number; subscriptions_monthly_total: number; subscriptions_annual_total: number; bills_monthly_total: number };
  insights: Insight[];
  scope: "personal" | "household";
  /** Present only in the household view: who spent what over the loaded window. */
  household?: {
    name: string; role: string;
    split: { user_id: number; name: string; spent: number; share_pct: number | null }[];
  };
};

export type Txn = {
  id: number; date: string; merchant: string; description: string; account: string | null; amount: number;
  category: string; category_source: string; is_transfer: boolean;
};

export type Subscription = {
  key: string; merchant: string; account: string | null; category: string; kind: "bill" | "subscription" | "recurring";
  cadence: string; occurrences: number; first_charge: string; last_charge: string; next_expected: string | null;
  last_amount: number; previous_price: number; price_changed_on: string | null; average_amount: number;
  monthly_cost: number; annual_cost: number; active: boolean; status: string;
};

export type SubFlag = {
  type: string; severity: Insight["severity"]; title: string; detail: string; merchant: string | null; key: string | null;
  merchants: string[]; potential_monthly_savings: number; potential_annual_savings: number;
};

export type Anomaly = { id: number; date: string; merchant: string; category: string; amount: number; reasons: string[]; severity: string };

export type BudgetItem = {
  category: string; limit: number; spent: number; remaining: number; used_pct: number | null;
  projected_month_end: number; status: "over" | "at_risk" | "on_track"; daily_allowance: number;
};
export type BudgetStatus = { month: string; days_elapsed: number; days_in_month: number; items: BudgetItem[]; total_limit: number; total_spent: number };

export type Suggestion = {
  category: string; avg_monthly: number; median_monthly: number; p75_monthly: number; suggested: number;
  method: string; rationale: string; current_budget: number | null; change_vs_avg: number;
};
export type Suggestions = {
  based_on_months: string[]; avg_monthly_income: number; suggestions: Suggestion[]; total_suggested: number;
  projected_monthly_savings: number;
  rule_50_30_20: { needs: number; needs_pct: number; wants: number; wants_pct: number; savings: number; savings_pct: number };
};

export type Forecast = {
  as_of: string; days: number; starting_balance: number; ending_balance: number; lowest_balance: number;
  lowest_balance_date: string; scheduled_income: number; scheduled_recurring_charges: number;
  estimated_variable_spending: number; avg_daily_variable_spend: number;
  series: { date: string; balance: number; events: { name: string; amount: number }[] }[];
};

export type Goal = {
  id: number; name: string; target: number; saved: number; remaining: number; progress_pct: number | null;
  target_date: string; months_left: number; required_monthly: number; available_monthly_surplus: number;
  on_track: boolean; months_to_goal_at_full_surplus: number | null;
};

export type Simulation = {
  cancelled_subscriptions: { merchant: string; key: string; monthly_cost: number }[];
  category_cuts: { category: string; cut_pct: number; avg_monthly: number; monthly_savings: number }[];
  monthly_savings: number; annual_savings: number; five_year_savings_at_4pct: number;
  monthly_surplus_before: number; monthly_surplus_after: number;
  goal_impact: { goal: string; months_before: number | null; months_after: number | null }[];
};

export type Debt = { id: number; name: string; kind: string; balance: number; apr_pct: number; min_payment: number; monthly_interest_now: number };
export type DebtSummary = { debts: Debt[]; total_debt: number; weighted_apr_pct: number; total_min_payment: number; monthly_interest_now: number };
export type Strategy = {
  description: string; feasible: boolean; months: number | null; debt_free_date: string | null;
  total_interest: number; total_paid: number; interest_saved_vs_minimum: number | null; months_saved_vs_minimum: number | null;
  first_win_month: number | null; payoff_order: { name: string; month: number; date: string }[];
};
export type DebtPlan = DebtSummary & {
  extra_monthly: number; recommended: "avalanche" | "snowball" | null; avg_monthly_surplus: number | null;
  avalanche_vs_snowball_interest?: number;
  strategies: Partial<Record<"avalanche" | "snowball" | "minimum", Strategy>>;
  chart: { month: string; avalanche: number; snowball: number; minimum: number }[];
};

export type NetWorth = {
  as_of: string; net_worth: number; total_assets: number; total_liabilities: number; cash: number; manual_assets: number;
  card_balances: number; debts: number; debt_to_asset_pct: number | null;
  asset_mix: { kind: string; value: number; share_pct: number | null }[];
  assets: { id: number; name: string; kind: string; value: number; updated_at: string }[];
  cash_history: { month: string; net_cash: number; partial: boolean }[];
  cash_change_period: number;
};

export type CalendarEvent = { name: string; amount: number; kind: "bill" | "subscription" | "recurring" | "income"; status: "posted" | "upcoming" };
export type BillCalendar = {
  month: string; as_of: string; first_weekday: number; days_in_month: number;
  days: { date: string; events: CalendarEvent[] }[];
  recurring_out: number; recurring_in: number; still_due: number; net_recurring: number; event_count: number;
  heaviest_day: string | null; earliest_month: string; latest_month: string;
};

export type Challenge = {
  id: number; type: string; title: string; params: Record<string, string | number>; start_date: string; end_date: string;
  days_total: number; days_elapsed: number; days_left: number; status: "active" | "won" | "lost";
  progress_pct: number; detail: string; estimated_savings: number;
};
export type ChallengeSuggestion = { type: string; title: string; params: Record<string, string | number>; duration_days: number; why: string; projected_savings: number };
export type Challenges = {
  as_of: string; items: Challenge[]; active: number; won: number; lost: number; win_streak: number;
  estimated_savings_total: number; suggestions: ChallengeSuggestion[];
};

export type Wrapped = {
  label: string; start: string; end: string; months: number; income: number; spending: number; saved: number;
  savings_rate_pct: number | null; spending_change_pct: number | null; persona: string; persona_tagline: string;
  top_category: { name: string; amount: number; share_pct: number | null } | null;
  top_discretionary_category: { name: string; amount: number } | null;
  top_merchant: { name: string; amount: number; visits: number } | null;
  most_visited: { name: string; visits: number; amount: number } | null;
  biggest_purchase: { merchant: string; amount: number; date: string } | null;
  priciest_month: { month: string; amount: number } | null; leanest_month: { month: string; amount: number } | null;
  coffee: { visits: number; amount: number }; delivery: { orders: number; amount: number };
  subscriptions_total: number; no_spend_days: number; longest_no_spend_streak: number; days: number;
  busiest_weekday: string | null; unique_merchants: number; monthly: { month: string; spending: number }[]; share_text: string;
};

export type BillingStatus = {
  configured: boolean; webhooks_configured: boolean; test_mode: boolean; plan: string; currency: string;
  /** subscription = auto-renewing; prepaid = one-time passes (Razorpay Subscriptions unavailable); null = payments off */
  mode: "subscription" | "prepaid" | null;
  pass: { tier: string; starts_at: number; ends_at: number; passes: number; days_left: number } | null;
  subscription: {
    id: string; tier: string; period: "monthly" | "annual"; amount: number; status: string;
    current_period_end: number | null; cancel_at_period_end: boolean; has_access: boolean; manage_url: string | null;
  } | null;
  payments: { id: string; amount: number; currency: string; status: string; method: string | null; date: number }[];
};

export type CheckoutSession = {
  mode: "subscription" | "order"; subscription_id?: string; order_id?: string;
  key_id: string; amount: number; currency: string; name: string; description: string;
  prefill: { name: string; email: string | null };
};

export type PlanCatalog = {
  current: string; currency: string;
  usage: { ai_questions_this_month: number; ai_questions_limit: number | null };
  plans: {
    id: string; name: string; price_monthly: number; price_annual: number; annual_saving_pct: number; tagline: string; coming_soon?: boolean;
    limits: { ai_questions_per_month: number | null };
    features: { key: string; label: string; included: boolean; tier: string }[];
  }[];
};

export type ChatReply = {
  thread_id: string; answer: string; tools_used: string[]; grounded: boolean; ungrounded_figures: string[];
  grounding_retried: boolean; mode: "llm" | "offline";
};

// ----------------------------------------------------------------- automations

export type RuleType = {
  key: string; label: string; description: string;
  params: { name: string; type: "money" | "days" | "category" | "percent"; label: string; default: number | string }[];
};

export type AlertRule = {
  id: number; kind: string; label: string; active: boolean;
  params: Record<string, string | number>; channels: string[];
};

export type Automations = {
  rules: AlertRule[];
  types: RuleType[];
  digest_period: "off" | "weekly" | "monthly";
  last_run_at: string | null;
  /** `rules: null` means unlimited; `email`/`digest` are false until the plan includes them. */
  limits: { rules: number | null; email: boolean; digest: boolean };
};

export type AlertNotification = {
  id: number; kind: string; severity: "high" | "medium" | "low" | "info";
  title: string; body: string; action_url: string | null; read: boolean; created_at: string;
};

export type Inbox = { unread: number; items: AlertNotification[] };

// ----------------------------------------------------------------- household

export type HouseholdMember = {
  user_id: number; name: string; email: string | null; role: "owner" | "member" | "viewer"; joined_at: string;
};

export type HouseholdView = {
  /** null when the user isn't in one; `can_create` says whether their plan allows starting one. */
  household: {
    id: number; name: string; role: "owner" | "member" | "viewer";
    is_owner: boolean; can_write: boolean; owner_id: number;
  } | null;
  can_create?: boolean;
  members: HouseholdMember[];
  invites: { email: string; role: string; created_at: string; expires_at: string }[];
  max_members: number;
  plan?: string;
};

export type OwnAccount = {
  id: number; name: string; type: string; institution: string | null;
  opening_balance_cents: number; shared: boolean;
};

export type InvitePreview = { household: string; role: string; email: string; invited_by: string | null };

// ----------------------------------------------------------------- freelancer & tax

export type TaxProfile = {
  regime: string; financial_year: string; business_share_default: number; gst_registered: boolean;
};

export type DeductionBucket = {
  key: string; label: string; amount: number; amount_text: string; transactions: number; share_pct: number | null;
};

export type BusinessSummary = {
  financial_year: string;
  gross_receipts: number; total_expenses: number; net_profit: number; margin_pct: number | null;
  buckets: DeductionBucket[];
  clients: { client: string; amount: number; amount_text: string; invoices: number; share_pct: number | null }[];
  tagged_transactions: number; untagged_spend: number;
};

export type TaxEstimate = {
  regime: string; regime_label?: string; applicable: boolean; eligible?: boolean;
  eligibility_note?: string | null; reason?: string;
  financial_year?: string; rates_from?: string;
  /** true when the estimate used an older year's published rates — the UI must say so. */
  rates_stale?: boolean;
  taxable_income?: number; taxable_income_text?: string; presumptive_share_pct?: number | null;
  bands?: { from: number; to: number | null; rate_pct: number; taxable: number; tax: number }[];
  tax_before_rebate?: number; rebate?: number; cess?: number;
  total_tax?: number; total_tax_text?: string;
  already_paid?: number; outstanding?: number; outstanding_text?: string;
  effective_rate_pct?: number | null;
  disclaimer: string;
};

export type AdvanceTax = {
  required: boolean; reason?: string;
  instalments: { due_date: string; share_pct: number; cumulative: number; instalment: number;
                 instalment_text: string; status: "paid" | "overdue" | "upcoming" }[];
  paid?: number; due_so_far?: number; shortfall?: number; shortfall_text?: string; note?: string;
};

export type TaxSuggestion = {
  transaction_id: number; date: string; merchant: string; category: string;
  amount: number; amount_text: string; deduction: string; deduction_label: string;
  suggested_share: number; why: string;
};

export type TaxOverview = {
  profile: TaxProfile; currency: string; financial_year: string; available_years: string[];
  summary: BusinessSummary; estimate: TaxEstimate;
  comparison: { regime: string; label: string; total_tax: number; total_tax_text: string;
                taxable_income: number; rates_stale: boolean; best?: boolean }[];
  advance_tax: AdvanceTax;
  payments: { id: number; paid_on: string; amount: number; amount_text: string; kind: string; note: string | null }[];
  suggestions: TaxSuggestion[];
  regimes: { key: string; label: string; description: string }[];
  deductions: { key: string; label: string; hint: string; default_share: number }[];
  receipts: { business_transactions: number; with_receipt: number; coverage_pct: number | null };
  disclaimer: string;
};

export type TaxTxn = {
  id: number; date: string; merchant: string; description: string; category: string; amount: number;
  kind: "business" | "personal" | null; deduction: string | null; share_pct: number;
  client: string | null; source: string | null; has_receipt: boolean;
};

export type AffordResult = {
  as_of: string; label: string | null; amount: number; date: string; recurring: boolean; annual_cost: number | null;
  verdict: "comfortable" | "tight" | "not_now"; reasons: string[]; wait_until: string | null;
  lowest_balance_before: number; lowest_balance_before_date: string;
  lowest_balance_after: number; lowest_balance_after_date: string;
  cushion: number; cushion_days: number;
  safe_to_spend_before: number; safe_to_spend_after: number;
  monthly_surplus_before: number; monthly_surplus_after: number;
  emergency_fund_months_before: number | null; emergency_fund_months_after: number | null;
  goals: { goal: string; months_before: number | null; months_after: number | null; delay_months: number | null;
           on_track_before: boolean; on_track_after: boolean }[];
  series: { date: string; before: number; after: number }[];
};

export type CapturePreviewItem = {
  text: string; ok: boolean; reason: string | null; date: string | null; amount: number | null; payee: string | null;
  mode: string | null; account_last4: string | null; reference: string | null; description: string | null;
  is_transfer: boolean; date_assumed: boolean; warnings: string[]; merchant?: string; category?: string;
};

export type SplitBalance = {
  person: string; items: number; vpa: string | null; oldest: string; net: number; net_text: string;
  direction: "owes_you" | "you_owe" | "even"; pay_link: string | null; reminder: string | null; reminder_pay_link: string | null;
};

export type SplitsOverview = {
  your_upi_vpa: string | null; owed_to_you: number; you_owe: number; net: number;
  balances: SplitBalance[];
  open: { id: number; person: string; amount: number; amount_text: string; direction: "owes_you" | "you_owe";
          note: string | null; transaction_id: number | null; merchant: string | null; date: string }[];
  settled: { person: string; amount: number; note: string | null; settled_at: string }[];
  this_month: { month: string; spending: number; fronted_for_others: number; your_share: number };
};

export type SecurityOverview = {
  mfa: { enabled: boolean; enabled_at: string | null; recovery_codes_left: number };
  sessions: { id: string; device: string; user_agent: string | null; ip: string | null; created_at: string;
              last_seen_at: string; expires_at: string; current: boolean }[];
  events: { kind: string; ip: string | null; user_agent: string | null; created_at: string; device: string }[];
};
