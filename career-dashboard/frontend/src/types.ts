export type Job = {
  id: string;
  company: string;
  title: string;
  location: string;
  market?: "ie" | "us";
  url: string;
  description: string;
  status: string;
  notes: string;
  application_date: string | null;
  folder: string | null;
  created_at: string;
  selected_project_id: string | null;
  record_source: "posting" | "gmail" | "posting+gmail";
  deleted_at: string | null;
  deletion_reason: string;
  posting_state?: "active" | "expired" | "needs_review";
  last_verified_at?: string | null;
  legitimacy_state?: "verified" | "needs_review" | "blocked" | null;
  size_category?: "startup" | "mid" | "large" | "unknown" | null;
  sponsorship_state?: string | null;
  sponsor_tier?: SponsorTier | null;
  sponsor_evidence?: SponsorEvidence | null;
  fit_score?: number | null;
  fit_rationale?: string | null;
  closed_at?: string | null;
  opportunity?: Opportunity | null;
};
export type SalarySource = {
  url: string;
  title: string;
  quote: string;
  observed_at: string;
};
export type SearchCoverage = {
  sources: {
    key: string;
    cursor: Record<string, unknown>;
    state: "partial" | "complete" | "failed" | "blocked" | "attempted";
    checked_at: string | null;
    found: number;
    error: string | null;
  }[];
  complete: number;
  partial: number;
  failed: number;
  scope: string;
};
export type Opportunity = {
  salary: {
    kind: "advertised" | "researched" | "unknown";
    currency: string;
    minimum: number | null;
    maximum: number | null;
    annual_min: number | null;
    annual_max: number | null;
    period: string;
    quote: string;
    url: string;
    sources: SalarySource[];
    observed_at: string;
  };
  salary_state: "meets_floor" | "below_floor" | "needs_confirmation" | "unknown";
  section: "salary_matches" | "estimated_matches" | "researched_leads" | "needs_research" | "below_floor";
  salary_policy?: "confirmed_or_estimated" | "confirmed_only";
  /** A market estimate for a posting that states no pay (backend/market/salary_estimates.py); never this vacancy's pay. */
  estimate?: { kind: "estimated"; currency: "EUR"; role_family: string; level: string; p25: number; median: number; p75: number; observations: number; employers: number; window_days: number; label: string } | null;
  permit: {
    state: "needs_confirmation" | "obstacle" | "criteria_checked";
    summary: string;
    checks: { id: string; label: string; state: "pass" | "unknown" | "fail"; note: string; url?: string }[];
    sources: { url: string; title: string }[];
    /** Dated facts only (backend/permits/timeline.py): a confirmed expiry, the graduate window, lead times. */
    timeline?: { as_of: string; events: PermitDate[]; disclaimer: string };
  };
  floor: number;
  sponsorship: { state: string; quote: string };
  /** backend/permits/path_score.py: public evidence for a permit route, 0-100; not approval likelihood. */
  permit_path?: { score: number; label: string; version: string; threshold: number; disclaimer: string;
    parts: { id: string; points: number; max: number; reason: string }[] };
};
export type PermitDate = { id: string; label: string; date: string | null; days_left: number | null; note: string; url?: string };
export type SponsorTier = "S" | "A" | "B" | "C";
export type SponsorEvidence = {
  tier?: SponsorTier | "EXCLUDED";
  label?: string;
  reason?: string;
  sentence?: string;
  cap_exempt?: boolean;
  cap_exempt_reason?: string;
  h1b_found?: boolean;
  h1b_approvals?: number;
  h1b_years?: string[];
  everify?: boolean;
};
export type ExcludedJob = {
  id: string;
  company: string;
  title: string;
  location: string;
  url: string;
  reason: string;
  reason_label: string;
  sentence: string;
  pattern?: string;
  source: string;
  excluded_at: string;
  restored_at?: string | null;
};
export type ApplicationDocuments = {
  job_id: string;
  company: string;
  title: string;
  resumes: { label: string; path: string }[];
  cover_letter: {
    version: number;
    path: string;
    created_at: string;
  } | null;
};
export type CoverLetter = {
  job_id: string;
  company: string;
  title: string;
  version: number;
  content: string;
  path: string;
  /** The Word copy under data/output ("" when Word copies are switched off). */
  docx_path?: string;
  created_at: string;
  /** "ai": drafted by the AI and checked against the evidence; "template": registered sentences. */
  method?: "ai" | "template";
  evidence_ids?: string[];
  company_facts?: { text: string; quote: string; url: string }[];
  /** Why an AI draft was set aside, when it was. */
  note?: string;
  review_required: boolean;
};
export type Report = {
  summary: string;
  report: string;
  verdict?: "pass" | "review" | "blocked";
  issues?: string[];
  sources: { title: string; url: string; accessed_at: string }[];
  limitations: string[];
};
export type Run = {
  id: string;
  kind: string;
  job_id: string | null;
  state: string;
  result: any;
  error: string | null;
  created_at: string;
  updated_at: string;
  provider?: string | null;
  model?: string | null;
  preset?: string;
};
export type Mail = {
  id: string;
  job_id: string | null;
  company: string;
  role: string;
  kind: string;
  subject: string;
  sender: string;
  received_at: string;
  submission_date: string | null;
  excerpt: string;
  reason: string;
  confidence: string;
  state: string;
};
export type Goals = {
  date: string;
  weekly_target: number;
  current_week_target: number;
  week_completed: number;
  daily_base: number;
  carryover: number;
  ahead: number;
  today_target: number;
  today_completed: number;
  remaining_today: number;
  week_remaining: number;
  schedule: {
    date: string;
    label: string;
    planned: number;
    completed: number;
    today: boolean;
  }[];
  settings: { weekly_target: number; workdays: number[]; start_date: string };
};
export type Agent = {
  id: string;
  name: string;
  reads: string;
  profile_access: boolean;
  does: string;
  implementation: string;
  guide: string;
};
export type AssuranceClaim = {
  text: string;
  section: string;
  origin: "verified" | "predicted" | null;
  evidence_status: "verified" | "predicted" | "missing";
  confidence: number;
  decision: "kept" | "removed" | "pending" | null;
  items: string[];
  evidence_ids: string[];
  line?: number | null;
  note?: string;
  /** A project bullet reworded for this job: its registered wording (the rewording passed the evidence check). */
  reworded_from?: string | null;
};
export type AssuranceReport = {
  job_id: string;
  company: string;
  role: string;
  generated_at: string;
  summary: {
    verified: number;
    predicted: number;
    missing: number;
    kept: number;
    removed: number;
    pending: number;
  };
  claims: AssuranceClaim[];
  score: number | null;
  note: string | null;
};
export type Summary = {
  jobs: Job[];
  removed_jobs: Job[];
  expired_jobs: Job[];
  excluded_jobs: ExcludedJob[];
  documents: ApplicationDocuments[];
  goals: Goals;
  mail: {
    connection: {
      connected: boolean;
      email?: string;
      last_synced_at?: string;
      coverage?: string;
      mode?: string;
      status?: string;
      last_attempt_at?: string;
      last_error?: string;
    };
    messages: Mail[];
    /** Whether optional mail integration is configured for this profile. */
    available?: boolean;
    note?: string;
  };
  runs: Run[];
  agents: Agent[];
  profile_dirty: boolean;
  counts: {
    saved: number;
    applied: number;
    interviews: number;
    offers: number;
    excluded: number;
    ghosted: number;
  };
  activity: { id: number; action: string; occurred_at: string; details: any }[];
  /** Workspace-wide notices, for example Irish permit thresholds that are due for review. */
  notices?: { id: string; level: "warning" | "info"; text: string }[];
  /** What only the person can do now (backend/services/needs_you.py), most urgent first. */
  needs_you?: NeedsYouItem[];
};
export type Knowledge = {
  id: string;
  kind: string;
  title: string;
  summary: string;
  data: Record<string, any>;
  source: string;
  revision: number;
  review_state: string;
  deleted: boolean;
};
/** One form field, as defined by backend/services/profile_fields.py. */
export type ProfileFieldSpec = {
  key: string;
  label: string;
  type: "text" | "textarea" | "list" | "number" | "boolean" | "select";
  options?: string[];
  /** The words shown for each stored option value. */
  option_labels?: Record<string, string>;
  required?: boolean;
  placeholder?: string;
  hint?: string;
};
export type ProfileFieldValue = string | number | boolean | null | string[];
/** A knowledge row plus the readable view the Profile page renders. */
export type ProfileEntry = Knowledge & {
  label: string;
  group: string | null;
  status: string | null;
  usage: string;
  sources: string[];
  fields: Record<string, ProfileFieldValue>;
  form: ProfileFieldSpec[];
  extra: { key: string; label: string; value: unknown }[];
  /** False when a summary-only save changed wording the form keeps elsewhere. */
  in_sync: boolean;
  /** Why the entry can't be removed (every resume or the job search needs it), or null. */
  locked?: string | null;
  /** For a project: why it is kept off resumes for now, or null. */
  off_resumes?: string | null;
};
/** Where a Profile save was written: the YAML files, the base resume and open drafts. */
export type ProfileSync = {
  updated: string[];
  drafts: { job_id: string; company: string; title: string }[];
  notes?: string[];
  revision: string;
} | null;
export type ProfileData = {
  items: ProfileEntry[];
  schema: Record<string, ProfileFieldSpec[]>;
  removed: number;
  registry: Record<string, any>;
  configuration: Record<string, any>;
  sources: Record<string, string>;
  agents: Agent[];
  profile_dirty: boolean;
  pending: { id: string; kind: string; title: string; label: string; deleted: boolean; source: string }[];
  revision: number;
  skills: { name: string; purpose: string; path: string }[];
};
export type AssistantStep = {
  at: string;
  label: string;
  state: "running" | "done" | "failed";
  detail: string;
  agent?: string;
  run_id?: string;
};
export type AssistantMessage = {
  id: string;
  message: string;
  response: string;
  state: "processing" | "done" | "needs_input" | "failed";
  steps: AssistantStep[];
  data: {
    intent?: string;
    job_id?: string | null;
    job_ids?: string[];
    company?: string;
    title?: string;
    tier?: string;
    revision?: number;
    pdf?: string | null;
    preview_png?: string | null;
    posting_url?: string;
    coverage?: number | null;
    ats?: number | null;
    gaps?: string[];
    warnings?: string[];
    suggestions?: string[];
    run_id?: string;
    /** Field-level before/after shown on a confirmation card. */
    diff?: { field: string; before: string; after: string }[];
    /** A reply that read several jobs' resumes: one compact row per job instead of one card. */
    cards?: {
      job_id: string;
      company: string;
      title: string;
      tier?: string | null;
      revision?: number | null;
      pdf?: string | null;
      coverage?: number | null;
      ats?: number | null;
      posting_url?: string | null;
    }[];
    /** Plan→calls→results record of an agent run ("what I did"). */
    trace?: { tool: string; summary: string; error?: boolean; auto_applied?: boolean }[];
    [key: string]: unknown;
  };
  created_at: string;
  updated_at: string;
};
export type AssistantEngine = {
  provider: string;
  model: string;
  label: string;
  ready: boolean;
  moved_from: string | null;
  /** What the runs the chat starts go through (the Settings main choice). */
  runs: { provider: string; model: string; label: string; ready: boolean };
  /** The configured backup provider, if any. */
  fallback: { provider: string; model: string } | null;
  /** The most recent automatic switch to the backup provider, if ever. */
  last_fallback: {
    from_provider: string;
    to_provider: string;
    reason?: string;
    at?: string;
  } | null;
  note: string | null;
  options: { provider: string; model: string; label: string }[];
};
export type AssistantAgent = {
  id: string;
  name: string;
  does: string;
  implementation: string;
  linked: boolean;
  tools: string[];
};
export type AssistantConversation = {
  id: string;
  /** The first line of the first message, cut short. */
  title: string;
  count: number;
  started_at: string;
  updated_at: string;
  busy: boolean;
  current: boolean;
};
export type AssistantOverview = {
  /** The open conversation; `messages` are its messages, `conversations` lists every thread. */
  conversation_id: string;
  conversations: AssistantConversation[];
  messages: AssistantMessage[];
  pending: {
    kind: string;
    question?: string;
    candidates?: { id: string; company: string; title: string }[];
    [key: string]: unknown;
  } | null;
  busy: boolean;
  /** May confirmation-gated tools run without the yes/no pause in this chat? */
  auto_apply: boolean;
  ai_configured: boolean;
  engine: AssistantEngine;
  agents: AssistantAgent[];
  capabilities: { group: string; labels: string[] }[];
};

// Daily Search pipeline (backend/services/pipeline.py).
export type PipelineSource = { id: string; label: string; what: string; ai: boolean };
export type PipelineFindCost = {
  minutes: number;
  minutes_per_job: number;
  tokens: number;
  tokens_per_job: number;
  budget_calls: number;
};
export type PipelineStepInfo = {
  id: string;
  label: string;
  what: string;
  ai: boolean;
  web: boolean;
  minutes: number;
  tokens: number;
  budget_calls: number;
};
export type PipelineModel = { id: string; label: string; hint: string };
// Auto: the route across her AI plans (backend/ai/router.py).
export type RouteEndpoint = {
  provider: string;
  label: string;
  position: number;
  enabled: boolean;
  ready: boolean;
  paid: boolean;
  models: { strong: string; cheap: string };
  capabilities: string[];
  resting: { until: string; until_text: string; reason: string; kind: string } | null;
  last_ok: string | null;
  last_error: string | null;
  served_today: number;
  paid_block: string | null;
  /** The plan's current 5-hour window (free plans only; tokens are metered estimates). */
  usage: {
    tokens: number;
    calls: number;
    capacity: number | null;
    capacity_source: "you" | "learned" | "guess";
    learned_at: string | null;
    percent: number | null;
    window_resets: string | null;
    resets_text: string;
  } | null;
};
export type RoutePolicy = {
  order: string[];
  enabled: Record<string, boolean>;
  allow_fallbacks: boolean;
  /** Tokens per 5-hour window she typed per free plan. */
  capacity?: Record<string, number | null>;
};

export type PipelineProvider = {
  id: string;
  label: string;
  kind: "local" | "api" | "auto";
  ready: boolean;
  web: boolean;
  cost: string;
  note: string | null;
  models: PipelineModel[];
  route?: RouteEndpoint[];
};
// tokens: measured tokens per job for this AI (the tailor reports usage), when known.
export type PipelineSpeed = { factor: number; learned: boolean; runs: number; tokens?: number };
/** One thing a posting asks for, quoted from it, and whether her registered evidence meets it. */
export type FitRequirement = {
  text: string;
  category: "required" | "preferred" | "responsibility";
  excerpt: string;
  /** "unknown": the rules could not name it, so it is not scored. */
  status: "met" | "partial" | "missing" | "unknown";
  evidence_ids: string[];
  note: string;
};
/** The verified requirement check for one job (GET /v2/jobs/{id}/fit, services/fit.py). */
export type JobFit = {
  matrix: { requirements: FitRequirement[]; hard_blockers: { excerpt: string; reason: string }[]; summary: string };
  method: "ai" | "rules";
  provider: string;
  model: string;
  provider_label: string;
  score: number;
  components: { requirements: number; role_seniority: number; location: number };
  must_have_ok: boolean;
  rationale: string;
  ai_error: string;
  checked_at?: string;
};
export type PipelineChoice = {
  count: number;
  source: string;
  provider: string;
  model: string;
  steps: Record<string, boolean>;
};
export type PipelineStepState = {
  state: "waiting" | "running" | "done" | "failed" | "skipped";
  seconds?: number;
  note?: string;
  error?: string;
  started_epoch?: number;
  quick?: boolean;
  found?: number;
  /** The ready-to-submit check's verdict and readiness score (backend/services/readiness.py). */
  verdict?: ReadinessVerdict;
  score?: number | null;
  /** What is left before sending, most important first. */
  next?: string[];
  /** Each search pass of a Daily Search that came back short and looked again. */
  passes?: { label: string; state: string; found?: number; looked?: number; seconds?: number; error?: string }[];
};
export type ReadinessVerdict = "ready" | "review" | "blocked";
/** GET /studio/<job>/readiness: the final check before a resume is sent. */
export type Readiness = {
  job_id: string;
  verdict: ReadinessVerdict;
  label: string;
  score: number | null;
  parts: { fit: number | null; coverage: number | null; ats: number | null };
  weights: { fit: number; coverage: number; ats: number };
  checks: { id: string; label: string; state: "pass" | "warn" | "fail"; note: string }[];
  next: string[];
  review_summary: string | null;
  note: string;
  checked_at: string;
};
export type PipelineJobProgress = {
  id: string;
  company: string;
  title: string;
  steps: Record<string, PipelineStepState>;
};
export type PipelineRun = {
  id: string;
  state: "queued" | "running" | "completed" | "failed" | "stopped";
  config: PipelineChoice;
  progress: {
    stage: string;
    jobs_target: number;
    started_epoch: number;
    finished_epoch?: number;
    find: PipelineStepState;
    jobs: PipelineJobProgress[];
    /** How many prepared jobs each ready-to-submit verdict got. */
    ready?: Partial<Record<ReadinessVerdict, number>>;
  };
  error: string | null;
  stop_requested: boolean;
  created_at: string;
  finished_at: string | null;
};
export type PipelineStatus = {
  tools: { pdf: boolean };
  // The daily limit counts paid calls only (Azure, API keys); free plan calls are free_used.
  budget: { limit: number; used: number; remaining: number; paid_only?: boolean; free_used?: number };
  plan: { remaining_today: number };
  current: PipelineRun | null;
  last: PipelineRun | null;
};
export type PipelineInfo = PipelineStatus & {
  sources: PipelineSource[];
  find: Record<"find_ai" | "find_pages", PipelineFindCost> & Partial<Record<"find_feeds", PipelineFindCost>>;
  steps: PipelineStepInfo[];
  max_jobs: number;
  providers: PipelineProvider[];
  speeds: Record<string, Record<string, PipelineSpeed>>;
  preferences: PipelineChoice;
};

// The overnight hunt (backend/services/hunt.py): search until the goal is met or the time is up.
export type HuntChoice = {
  target: number;
  hours: number;
  min_fit: number;
  sources: "all" | "feeds" | "ai";
  allow_paid: boolean;
  require_ai_fit: boolean;
  steps: Record<string, boolean>;
  provider?: string;
  model?: string;
};
export type HuntPass = {
  id: string;
  label: string;
  kind: "feeds" | "ai";
  state: "running" | "done" | "failed" | "skipped" | "stopped";
  cycle?: number;
  looked?: number;
  saved?: number;
  turned_away?: number;
  excluded?: number;
  held?: number;
  ai_checked?: number;
  seconds?: number;
  error?: string;
  note?: string;
};
export type HuntSaved = {
  id: string;
  company: string;
  title: string;
  location?: string;
  fit?: number | null;
  url?: string;
  pass?: string;
};
export type HuntWaiting = { until: string; until_text: string; why: string };
export type HuntRun = {
  id: string;
  state: "queued" | "running" | "waiting" | "completed" | "failed" | "stopped" | "interrupted";
  config: HuntChoice;
  progress: {
    stage: string;
    target: number;
    cycle: number;
    saved: HuntSaved[];
    passes: HuntPass[];
    waiting: HuntWaiting | null;
    prepare?: { state: string; jobs: PipelineJobProgress[] };
    report?: string;
    started_epoch?: number;
    search_until_epoch?: number;
    deadline_epoch?: number;
    finished_epoch?: number;
    strategy_count?: number;
    exhausted?: boolean;
  };
  error: string | null;
  stop_requested: boolean;
  created_at: string;
  finished_at: string | null;
};
export type HuntStatus = { current: HuntRun | null; last: HuntRun | null };
export type HuntInfo = HuntStatus & {
  defaults: HuntChoice;
  limits: { max_target: number; max_hours: number };
  plan: {
    roles: string[];
    related_titles: string[];
    excluded_titles: string[];
    markets: string[];
    board_keywords: string[];
    early_career: boolean;
    strategies: { id: string; kind: string; label: string; queries?: string[] }[];
    error?: string;
  };
  memory: { outcomes: Record<string, number>; rejected_by_stage: Record<string, number>; held: number };
  ai: { ready: boolean; wakes_at: string | null; wakes_text: string | null; note: string };
};

/** One Tracker row: an Irish posting the app read, with its permit evidence (market/tracker.py). */
export type TrackerRow = {
  key: string;
  title: string;
  company: string;
  url: string;
  location: string;
  counties: string[];
  region: string;
  posting_type: string;
  level: string;
  role_family: string;
  years_required: number | null;
  closing_date: string;
  posted_at: string;
  first_seen: string;
  last_seen: string;
  salary: { kind: string; min: number | null; max: number | null; currency: string; period: string; quote: string };
  statement: string;
  statement_quote: string;
  on_eures: boolean;
  source: string;
  source_label: string;
  source_kind: string;
  lead: boolean;
  attribution: string;
  sources: number;
  permits: { permits_24m: number | null; legal_names: string[]; by_year: Record<string, number> };
  tags: string[];
  mine: { job_id: string; status: string } | null;
  excluded: boolean;
};

export type TrackerResult = {
  rows: TrackerRow[];
  total: number;
  all: number;
  facets: {
    type: Record<string, number>;
    statement: Record<string, number>;
    county: Record<string, number>;
    dete: { yes: number };
    new: number;
    closing_soon: number;
    leads: number;
  };
  filters: Record<string, string>;
  updated: string;
  note: string;
  floor_eur: number;
};

export type TrackerAlert = {
  id: string;
  name: string;
  filters: Record<string, string>;
  created_at: string;
  seen_at: string;
  matches: number;
  new: number;
  examples: { title: string; company: string; key: string }[];
};

export type JobSourceStatus = {
  id: string;
  kind: string;
  label: string;
  ready: boolean;
  missing: string;
  terms: string;
  personal_use_only?: boolean;
  attribution?: string;
  attribution_url?: string;
  key_page?: string;
  budget?: { per_day: number; lifetime?: number };
  used?: { today: number; lifetime: number };
  keys: { name: string; saved: boolean; saved_in: string }[];
};

export type NeedsYouItem = { id: string; text: string; where: "profile" | "settings" | "daily"; url?: string; job_ids?: string[] };
