export type TaskStatus = 'todo' | 'doing' | 'done'
export type AgentStatus = 'waiting' | 'running' | 'completed' | 'warning' | 'failed'

export type CalendarRecurrenceType = 'none' | 'daily' | 'weekly' | 'monthly'
export interface CalendarRecurrenceRule { type: CalendarRecurrenceType; interval: number; days_of_week: number[]; until?: string | null; count?: number | null }
export interface CalendarEvent { id: number; title: string; member?: string; day: string; time: string; category: string; conflict: boolean; start_at?: string | null; end_at?: string | null; timezone: string; location: string; notes: string; recurrence: CalendarRecurrenceRule; occurrence_start_at?: string | null; occurrence_end_at?: string | null }
export interface MealItem { id: number; day: string; name: string; duration: number; cost: number; tags: string[]; reason: string; ingredients: string[] }
export type MealItemInput = Omit<MealItem, 'id'>
export interface ShoppingItem { id: number; name: string; category: string; quantity: string; price: number; source: string; purchased: boolean; actual_price?: number | null; verification_note?: string | null }
export type ShoppingItemInput = Omit<ShoppingItem, 'id'>
export interface KnowledgeDocument { id: string | number; name: string; category: string; status: string; chunks: number; updated_at: string }
export interface VectorSearchHit { document_id: string; document_name: string; category: string; content: string; chunk_index: number; score: number }
export interface GraphSearchHit { subject: string; relation: string; target: string; detail: string }
export interface RetrievalDiagnostics { chroma: string; neo4j: string; embedding: string; rerank?: string }
export interface KnowledgeSearchResponse { query: string; vector_hits: VectorSearchHit[]; graph_hits: GraphSearchHit[]; elapsed_ms: number; diagnostics: RetrievalDiagnostics }
export interface AIServiceStatus { rag_enabled: boolean; llm_mode: string; langgraph: string; chroma: string; neo4j: string; collection: string; documents: number; chunks: number; llm_provider: string; llm_model: string; llm_configured: boolean; redis: string; celery: string; embedding: string; reranker?: string }
export interface SyncConsistencyResponse { chroma_status: string; neo4j_status: string; chroma_documents: number; chroma_chunks: number; neo4j_documents: number; neo4j_entities: number; missing_in_neo4j: string[]; orphan_in_neo4j: string[]; consistent: boolean; notes: string[] }
export interface RagEvalResult { query: string; recall_at_k: number; ndcg_at_k: number; hit_document_names: string[]; hit_entity_kinds: string[] }
export interface RagEvalResponse { evaluated_at: string; embedding: string; reranker: string; top_k: number; case_count: number; mean_recall_at_k: number; mean_ndcg_at_k: number; results: RagEvalResult[]; notes: string[] }
export interface LLMSmokeResponse { status: string; provider: string; model: string; latency_ms: number; message: string }
export interface AgentStep { name: string; label: string; status: AgentStatus; duration_ms: number; summary: string; output: Record<string, unknown> }
export interface AgentRun { id: string; request: string; status: AgentStatus; started_at: string; finished_at: string | null; duration_ms: number; steps: AgentStep[]; error_message: string; error_type: string; failed_step: string; checkpoint?: Record<string, unknown> }
export interface BudgetSummary { limit: number; estimated: number; saved: number; usage_percent: number; categories: Record<string, number> }
export interface MealAgentResult { strategy: string; constraints_applied: string[]; excluded_ingredients: string[]; preferred_tags: string[]; max_duration_minutes: number }
export interface ShoppingAgentResult { strategy: string; merge_keys: string[]; preferred_categories: string[]; purchase_windows: string[] }
export interface BudgetAgentResult { strategy: string; limit: number; reserve: number; warning_threshold_percent: number; category_limits: Record<string, number> }
export interface DomainAgentBundle { meal: MealAgentResult; shopping: ShoppingAgentResult; budget: BudgetAgentResult; merged_constraints: string[] }
export interface ShoppingMergeResponse { merged_groups: number; removed_items: number; items: ShoppingItem[]; conversion_notes: { name: string; original: string; converted: string }[] }
export interface FeedbackSyncInfo { feedback_id: number; sentiment: 'positive' | 'neutral' | 'negative'; deviation: number; graph_synced: boolean; vector_synced: boolean; notes: string[] }
export interface FeedbackEntry { id: number; feedback_type: string; reference_type: string; reference_id: number; subject: string; tags: string[]; rating: number | null; sentiment: string; content: string; planned_value: number; actual_value: number; deviation: number; source: string; synced_to_graph: boolean; synced_to_vector: boolean; created_at: string }
export interface TasteProfileResponse { liked_tags: string[]; disliked_tags: string[]; liked_dishes: string[]; rejected_dishes: string[]; recent_notes: string[]; sample_size: number }
export interface FeedbackOverviewResponse { items: FeedbackEntry[]; sentiment_counts: Record<string, number>; pending_sync: number; taste_profile: TasteProfileResponse }
export interface MealReplacementResponse { meal: MealItem; feedback?: FeedbackSyncInfo; taste_profile: TasteProfileResponse }
export interface Recipe { id: number; name: string; description: string; ingredients: string[]; steps: string[]; tags: string[]; allergens: string[]; duration: number; estimated_cost: number; is_favorite: boolean; servings: number; nutrition: Record<string, number> }
export type RecipeInput = Omit<Recipe, 'id'>
export interface Dashboard { user_name: string; greeting: string; date_label: string; today_events: CalendarEvent[]; tonight_meal: MealItem; budget: BudgetSummary; notices: string[]; week_progress: number }
export interface PlanningResponse { run_id: string; summary: string; meals: MealItem[]; shopping: ShoppingItem[]; budget: BudgetSummary; conflicts: string[]; suggestions: string[]; domain: DomainAgentBundle; sources: string[]; trace: AgentStep[] }
export interface ChatMessage { id: number; role: 'user' | 'assistant' | 'system'; content: string; run_id: string | null; created_at: string }
export interface ChatSessionSummary { id: string; title: string; status: string; last_run_id: string | null; created_at: string; updated_at: string }
export interface ChatSessionDetail extends ChatSessionSummary { messages: ChatMessage[] }
export interface ChatTurnResponse { session: ChatSessionSummary; user_message: ChatMessage; assistant_message: ChatMessage; plan: PlanningResponse }
export interface BackgroundJob { id: string; kind: string; status: string; result: Record<string, unknown>; error_message: string; created_at: string; started_at: string | null; finished_at: string | null; idempotency_key: string | null; priority: string }
export interface QueueStats { name: string; depth: number; routing_key: string }
export interface CeleryStatsResponse { broker_connected: boolean; queues: QueueStats[]; status_counts: Record<string, number>; recent_jobs: BackgroundJob[]; dead_letter_count: number; result_expires: number; active_queues: string[] }
export interface DeadLetterItem { id: string; kind: string; error_message: string; priority: string; created_at: string; finished_at: string | null }
export type ChatStreamEvent = { event: 'message'; data: ChatMessage } | { event: 'step'; data: AgentStep } | { event: 'token'; data: { content: string } } | { event: 'complete'; data: { message: ChatMessage; plan: PlanningResponse } } | { event: 'cancelled' | 'error'; data: { message: string } }
export interface PlanTask { id: number; title: string; assignee: string; duration: number; due: string; status: TaskStatus; category: string }
export interface WeeklyPlanSummary { id: number; status: string; version: number; is_active: boolean; parent_plan_id: number | null; prompt: string; budget: number; summary: string; created_at: string; meal_count: number; task_count: number; shopping_count: number }
export interface WeeklyPlanDetail { id: number; status: string; version: number; is_active: boolean; parent_plan_id: number | null; prompt: string; budget: number; summary: string; conflicts: string[]; suggestions: string[]; run_id: string | null; created_at: string; updated_at: string; meals: MealItem[]; shopping: ShoppingItem[]; tasks: PlanTask[]; budget_record: BudgetSummary | null }
export interface UserSummary { id: number; phone: string; display_name: string }
export interface AuthSession { access_token: string; refresh_token: string; token_type: string; expires_in: number; user: UserSummary }
export interface CurrentSession { user: UserSummary; jwt_development_secret: boolean }
export interface SMSCodeResponse { message: string; expire_minutes: number; retry_after_seconds: number }
export interface DeviceSession { id: string; device_info?: string; created_at: string; expires_at: string }
export interface PlanDiffSection { added: Record<string, unknown>[]; removed: Record<string, unknown>[]; changed: { key: string; before: Record<string, unknown>; after: Record<string, unknown> }[] }
export interface PlanDiff { from_version: number; to_version: number; sections: Record<'meals' | 'shopping' | 'tasks', PlanDiffSection> }
export interface NutrientEntry { target: number; actual: number; percent: number; satisfied: boolean }
export interface NutritionReport { targets: Record<string, number>; actual: Record<string, number>; nutrients: Record<string, NutrientEntry>; overall_percent: number; satisfied: boolean; calibrated_meals: number; uncalibrated_meals: number; meal_count: number }
export interface AgentMetricDetail { score: number; metrics: Record<string, unknown>; issues: string[] }
export interface AgentEvaluation { overall_score: number; scores: Record<string, number>; details: Record<string, AgentMetricDetail>; issues: string[]; prompt_versions: Record<string, string> }
export interface PromptVersionInfo { name: string; version: string; system_message: string; instruction: string; changelog: string; released_at: string; is_active: boolean }
export interface PromptRegistryResponse { agents: Record<string, PromptVersionInfo[]>; active_versions: Record<string, string> }
export interface InventoryEntry { id: number; name: string; category: string; quantity: string; quantity_value: number; unit: string; low_stock_threshold: number; note: string; is_low_stock: boolean }
export interface InventoryAdjustInput { name: string; category?: string; delta: number; unit?: string; quantity?: string | null; low_stock_threshold?: number | null; note?: string }
export interface InventoryResponse { items: InventoryEntry[]; count: number; low_stock_count: number }
export interface ArchivedPlanResponse { id: number; status: string; is_active: boolean; archived_at: string }
