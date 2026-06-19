

CREATE FUNCTION public.audit_log_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION
              'audit_log is append-only; UPDATE/DELETE denied (row id=%)',
              OLD.id
              USING ERRCODE = 'check_violation';
        END;
        $$;

CREATE FUNCTION public.sync_doc_deleted_at_to_chunks() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                    UPDATE document_chunks
                    SET doc_deleted_at = NEW.deleted_at
                    WHERE record_document_id = NEW.id;
                END IF;
                RETURN NEW;
            END;
            $$;

CREATE FUNCTION public.update_chunk_search_vector() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.search_vector = to_tsvector(
                'simple',
                COALESCE(NEW.content_segmented, NEW.content, '')
            );
            RETURN NEW;
        END;
        $$;

CREATE TABLE public.ai_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_provider_id uuid NOT NULL,
    api_key_encrypted text NOT NULL,
    fingerprint character varying(32) NOT NULL,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    last_health_check_at timestamp with time zone,
    last_health_status character varying(32),
    last_used_at timestamp with time zone,
    rotated_at timestamp with time zone,
    rotated_by_user_id character varying(64),
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.ai_models (
    id uuid NOT NULL,
    record_provider_id uuid NOT NULL,
    name character varying(128) NOT NULL,
    kind character varying(32) NOT NULL,
    context_window integer NOT NULL,
    max_output_tokens integer NOT NULL,
    input_price_per_1k_usd numeric(10,6) NOT NULL,
    output_price_per_1k_usd numeric(10,6) NOT NULL,
    supports_streaming boolean NOT NULL,
    supports_tools boolean NOT NULL,
    supports_vision boolean NOT NULL,
    supports_json_mode boolean NOT NULL,
    languages character varying(8)[] NOT NULL,
    metadata_json jsonb NOT NULL,
    enabled boolean NOT NULL,
    model_id character varying(128),
    input_price_per_1k_cached_usd numeric(10,6),
    default_temperature numeric(3,2),
    default_top_p numeric(3,2),
    default_max_tokens integer,
    quality_tier character varying(16) NOT NULL,
    latency_p50_ms integer,
    latency_p95_ms integer,
    supports_caching boolean NOT NULL,
    supports_reasoning boolean NOT NULL,
    embedding_dimension integer,
    deprecation_date timestamp with time zone,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.ai_providers (
    id uuid NOT NULL,
    name character varying(64) NOT NULL,
    type character varying(32) NOT NULL,
    base_url text NOT NULL,
    auth_type character varying(32) NOT NULL,
    metadata_json jsonb NOT NULL,
    enabled boolean NOT NULL,
    code character varying(64),
    api_key_ref character varying(256),
    api_key_encrypted text,
    timeout_ms integer NOT NULL,
    connect_timeout_ms integer NOT NULL,
    max_retries integer NOT NULL,
    max_concurrent integer NOT NULL,
    healthcheck_url character varying(512),
    region character varying(32),
    requires_prefix boolean NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_provider_id uuid,
    provider_code character varying(64) NOT NULL,
    label character varying(64) DEFAULT 'primary'::character varying NOT NULL,
    value_plain text,
    value_encrypted text,
    active boolean DEFAULT true NOT NULL,
    rotation_state character varying(16) DEFAULT 'live'::character varying NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT api_keys_rotation_state_check CHECK (((rotation_state)::text = ANY ((ARRAY['live'::character varying, 'cooldown'::character varying, 'revoked'::character varying])::text[])))
);

CREATE TABLE public.api_tokens (
    id uuid NOT NULL,
    service_name character varying(128) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    token_hash character varying(64) NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    role character varying(16) DEFAULT 'service'::character varying NOT NULL,
    rate_limit_value integer DEFAULT 120 NOT NULL,
    rate_limit_window integer DEFAULT 60 NOT NULL
);

CREATE TABLE public.audit_log (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    actor_user_id character varying(128) NOT NULL,
    action character varying(32) NOT NULL,
    resource_type character varying(64) NOT NULL,
    resource_id character varying(128) NOT NULL,
    before_json jsonb,
    after_json jsonb,
    reason text,
    trace_id character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    row_hash character varying(64) NOT NULL
);

ALTER TABLE ONLY public.audit_log FORCE ROW LEVEL SECURITY;

CREATE TABLE public.bot_model_bindings (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid NOT NULL,
    purpose character varying(32) NOT NULL,
    record_model_id uuid NOT NULL,
    rank integer NOT NULL,
    variant character varying(16),
    weight integer NOT NULL,
    temperature numeric(3,2) NOT NULL,
    max_tokens integer NOT NULL,
    top_p numeric(3,2) NOT NULL,
    extra_params jsonb NOT NULL,
    active boolean NOT NULL,
    version integer NOT NULL,
    created_by character varying(128),
    record_fallback_model_id uuid,
    record_prompt_template_id uuid,
    record_prompt_version_id uuid,
    effective_from timestamp with time zone DEFAULT now() NOT NULL,
    effective_to timestamp with time zone,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.bot_model_bindings FORCE ROW LEVEL SECURITY;

CREATE TABLE public.bot_token_usage_log (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    bot_id character varying(64) NOT NULL,
    channel_type character varying(32) NOT NULL,
    record_bot_id uuid NOT NULL,
    usage_by_month jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.bots (
    id uuid NOT NULL,
    bot_id character varying(64) NOT NULL,
    channel_type character varying(32) NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_tenant_id uuid NOT NULL,
    bot_name character varying(255) NOT NULL,
    record_model_id uuid,
    record_embedding_model_id uuid,
    system_prompt text NOT NULL,
    setting_options jsonb NOT NULL,
    custom_vocabulary jsonb NOT NULL,
    max_history integer,
    max_documents integer NOT NULL,
    prompt_max_tokens integer,
    rerank_top_n integer,
    plan_limits jsonb NOT NULL,
    callback_url text,
    bypass_token_limit boolean NOT NULL,
    bypass_rate_limit boolean NOT NULL,
    tokens_used bigint DEFAULT 0 NOT NULL,
    extra_max_tokens bigint DEFAULT 0 NOT NULL,
    extra_output_tokens_per_response integer DEFAULT 0 NOT NULL,
    bypass_token_check boolean DEFAULT false NOT NULL,
    language character varying(8) NOT NULL,
    oos_answer_template character varying(1000),
    rerank_intent_whitelist jsonb,
    threshold_overrides jsonb NOT NULL,
    action_config jsonb,
    metadata_extraction_config jsonb,
    is_deleted boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_bot_id_not_empty CHECK ((length(TRIM(BOTH FROM bot_id)) > 0)),
    CONSTRAINT ck_bots_extra_max_tokens_nonneg CHECK ((extra_max_tokens >= 0)),
    CONSTRAINT ck_bots_extra_output_tokens_per_response_nonneg CHECK ((extra_output_tokens_per_response >= 0)),
    CONSTRAINT ck_bots_tokens_used_nonneg CHECK ((tokens_used >= 0))
);

ALTER TABLE ONLY public.bots FORCE ROW LEVEL SECURITY;

CREATE TABLE public.chat_histories (
    id bigint NOT NULL,
    record_bot_id uuid NOT NULL,
    channel_type character varying(64) DEFAULT 'web'::character varying NOT NULL,
    connect_id character varying(255) NOT NULL,
    role character varying(16) NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.chat_histories_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.chat_histories_id_seq OWNED BY public.chat_histories.id;

CREATE TABLE public.conversations (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid NOT NULL,
    connect_id character varying(255) NOT NULL,
    channel character varying(64) NOT NULL,
    rolling_summary text NOT NULL,
    turn_count integer NOT NULL,
    last_message_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata_json jsonb NOT NULL
);

ALTER TABLE ONLY public.conversations FORCE ROW LEVEL SECURITY;

CREATE TABLE public.document_chunks (
    id uuid NOT NULL,
    record_bot_id uuid,
    record_document_id uuid,
    chunk_index integer,
    metadata_json jsonb,
    chunk_type character varying(32) DEFAULT 'text'::character varying NOT NULL,
    content text,
    content_segmented text,
    content_hash character(64),
    parent_chunk_id uuid,
    chunk_chars integer,
    search_vector tsvector,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    chunk_context text,
    doc_deleted_at timestamp with time zone,
    embedding public.vector(1024)
);

ALTER TABLE ONLY public.document_chunks FORCE ROW LEVEL SECURITY;

CREATE TABLE public.document_service_index (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid NOT NULL,
    record_document_id uuid NOT NULL,
    record_chunk_id uuid,
    entity_name text NOT NULL,
    entity_category text,
    price_primary numeric,
    price_secondary numeric,
    attributes_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.documents (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid NOT NULL,
    source_url text NOT NULL,
    document_name character varying(255) NOT NULL,
    tool_name character varying(255) NOT NULL,
    mime_type character varying(128) NOT NULL,
    language character varying(8) NOT NULL,
    state character varying(32) NOT NULL,
    version integer NOT NULL,
    content_hash character varying(64) NOT NULL,
    acl character varying(255)[] NOT NULL,
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    content_chars integer,
    raw_content text,
    summary_json jsonb,
    access_groups text[] DEFAULT '{}'::text[],
    current_step character varying(32),
    progress_percent integer,
    chunks_total integer,
    chunks_processed integer,
    progress_updated_at timestamp with time zone
);

ALTER TABLE ONLY public.documents FORCE ROW LEVEL SECURITY;

CREATE TABLE public.event_inbox (
    subscriber_id character varying(255) NOT NULL,
    msg_id uuid NOT NULL,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.guardrail_events (
    event_id uuid NOT NULL,
    message_id bigint NOT NULL,
    record_request_id uuid,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_step_id uuid,
    guardrail_type character varying(32) NOT NULL,
    rule_id character varying(64) NOT NULL,
    severity character varying(16) NOT NULL,
    action_taken character varying(16) NOT NULL,
    details jsonb NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.guardrail_events FORCE ROW LEVEL SECURITY;

CREATE TABLE public.guardrail_rules (
    id uuid NOT NULL,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    rule_id character varying(64) NOT NULL,
    pattern text NOT NULL,
    pattern_flags character varying(32) NOT NULL,
    severity character varying(16) NOT NULL,
    action_taken character varying(16) NOT NULL,
    scope character varying(16) NOT NULL,
    enabled boolean NOT NULL,
    priority integer NOT NULL,
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.ingest_idempotency_keys (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    request_hash character varying(64) NOT NULL,
    record_document_id uuid,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

CREATE TABLE public.jobs (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    channel_type character varying(64),
    kind character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    payload jsonb NOT NULL,
    result jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone
);

ALTER TABLE ONLY public.jobs FORCE ROW LEVEL SECURITY;

CREATE TABLE public.knowledge_edges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_bot_id uuid NOT NULL,
    channel_type character varying(64) DEFAULT 'web'::character varying NOT NULL,
    subject text NOT NULL,
    relation text NOT NULL,
    object text NOT NULL,
    source_document text,
    source_chunk_id uuid,
    confidence double precision DEFAULT 1.0,
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY public.knowledge_edges FORCE ROW LEVEL SECURITY;

CREATE TABLE public.language_packs (
    code character varying(8) NOT NULL,
    prompt_key character varying(64) NOT NULL,
    content text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.messages (
    id uuid NOT NULL,
    record_conversation_id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid NOT NULL,
    role character varying(16) NOT NULL,
    content text NOT NULL,
    citations jsonb NOT NULL,
    tokens_used integer NOT NULL,
    cost_usd numeric(10,6) NOT NULL,
    channel character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.messages FORCE ROW LEVEL SECURITY;

CREATE TABLE public.model_capabilities (
    record_model_id uuid NOT NULL,
    tier character varying(16) NOT NULL,
    can_web_search boolean NOT NULL,
    can_read_private_docs boolean NOT NULL,
    can_reasoning boolean NOT NULL,
    can_tool_use boolean NOT NULL,
    can_vision boolean NOT NULL,
    quality_score numeric(3,1) NOT NULL,
    hallucination_rate numeric(5,2) NOT NULL,
    suitable_for character varying(32)[] NOT NULL,
    not_suitable_for character varying(32)[] NOT NULL,
    metadata_json jsonb NOT NULL,
    max_input_tokens integer,
    max_concurrent_per_key integer,
    rate_limit_rpm integer,
    rate_limit_tpm integer,
    supports_streaming boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by character varying(128)
);

CREATE TABLE public.model_invocations (
    invocation_id uuid NOT NULL,
    message_id bigint NOT NULL,
    record_request_id uuid,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    attempt_no integer NOT NULL,
    purpose character varying(32) NOT NULL,
    feature_name character varying(64),
    provider character varying(32) NOT NULL,
    model_id character varying(128) NOT NULL,
    model_version character varying(64),
    user_prompt_hash character(64),
    full_payload_hash character(64),
    response_hash character(64),
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    cost_usd numeric(12,6) NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    duration_ms integer NOT NULL,
    status character varying(16) NOT NULL,
    finish_reason character varying(32),
    cached boolean NOT NULL
);

ALTER TABLE ONLY public.model_invocations FORCE ROW LEVEL SECURITY;

CREATE TABLE public.module_permissions (
    id integer NOT NULL,
    module character varying(64) NOT NULL,
    permission character varying(64) NOT NULL,
    min_role_level integer NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.module_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.module_permissions_id_seq OWNED BY public.module_permissions.id;

CREATE TABLE public.monitoring_log (
    id bigint NOT NULL,
    request_id uuid,
    record_tenant_id uuid,
    record_bot_id uuid,
    bot_id character varying(255),
    workspace_id character varying(64),
    channel_type character varying(32),
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    duration_ms integer,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6) DEFAULT '0'::numeric NOT NULL,
    model_name character varying(128),
    status character varying(32),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.monitoring_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.monitoring_log_id_seq OWNED BY public.monitoring_log.id;

CREATE TABLE public.outbox (
    id uuid NOT NULL,
    subject character varying(128) NOT NULL,
    payload bytea NOT NULL,
    headers jsonb NOT NULL,
    trace_id character varying(128) NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    channel_type character varying(64),
    retry_count integer NOT NULL,
    status character varying(16) NOT NULL,
    last_error text,
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    redis_entry_id character varying(64)
);

ALTER TABLE ONLY public.outbox FORCE ROW LEVEL SECURITY;

CREATE TABLE public.prompt_templates (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    record_bot_id uuid,
    template_key character varying(64) NOT NULL,
    version integer NOT NULL,
    jinja_source text NOT NULL,
    required_vars character varying(64)[] NOT NULL,
    model_hint character varying(128),
    active boolean NOT NULL,
    created_by character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.prompt_templates FORCE ROW LEVEL SECURITY;

CREATE TABLE public.prompt_versions (
    id uuid NOT NULL,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    purpose character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    version_no integer NOT NULL,
    template text NOT NULL,
    variables jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.prompt_versions FORCE ROW LEVEL SECURITY;

CREATE TABLE public.quotas (
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    monthly_limit integer NOT NULL,
    used_tokens integer NOT NULL,
    used_cost_usd numeric(12,6) NOT NULL,
    reset_at timestamp with time zone DEFAULT now() NOT NULL,
    blocked boolean NOT NULL,
    documents_per_day_limit integer NOT NULL,
    documents_today_count integer NOT NULL,
    documents_today_reset_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.quotas FORCE ROW LEVEL SECURITY;

CREATE TABLE public.refuse_suggestions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_tenant_id uuid NOT NULL,
    record_bot_id uuid NOT NULL,
    query_intent character varying(64) NOT NULL,
    refuse_count integer DEFAULT 1 NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    sample_query text DEFAULT ''::text NOT NULL
);

ALTER TABLE ONLY public.refuse_suggestions FORCE ROW LEVEL SECURITY;

CREATE TABLE public.request_chunk_refs (
    id uuid NOT NULL,
    record_request_id uuid NOT NULL,
    record_chunk_id uuid NOT NULL,
    rank integer NOT NULL,
    score numeric(8,6),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.request_logs (
    request_id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64) NOT NULL,
    channel_type character varying(64),
    connect_id character varying(255) NOT NULL,
    record_bot_id uuid,
    record_conversation_id uuid,
    message_id bigint NOT NULL,
    context_namespace character varying(255),
    trace_id character varying(128) NOT NULL,
    question_hash character varying(64) NOT NULL,
    answer_hash character varying(64),
    refusal_reason text,
    record_model_id uuid,
    model_name character varying(128),
    routing_reason text,
    record_binding_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    duration_ms integer NOT NULL,
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    total_tokens integer NOT NULL,
    cost_usd numeric(12,6) NOT NULL,
    status character varying(16) NOT NULL,
    error_code character varying(64),
    error_message text,
    citations jsonb NOT NULL,
    feedback_score integer,
    is_correct boolean,
    quality_evaluated_at timestamp with time zone,
    quality_evaluator character varying(64),
    feedback_comment text,
    metadata_json jsonb NOT NULL
);

ALTER TABLE ONLY public.request_logs FORCE ROW LEVEL SECURITY;

CREATE TABLE public.request_steps (
    id uuid NOT NULL,
    record_request_id uuid NOT NULL,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    channel_type character varying(64),
    step_name character varying(64) NOT NULL,
    step_order integer NOT NULL,
    model_used character varying(128),
    record_binding_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    duration_ms integer NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    cost_usd numeric(12,6) NOT NULL,
    status character varying(16) NOT NULL,
    error text,
    metadata_json jsonb NOT NULL
);

ALTER TABLE ONLY public.request_steps FORCE ROW LEVEL SECURITY;

CREATE TABLE public.role_definitions (
    id integer NOT NULL,
    role_name character varying(32) NOT NULL,
    level integer NOT NULL,
    scope character varying(32) DEFAULT 'workspace'::character varying NOT NULL,
    description text,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.role_definitions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.role_definitions_id_seq OWNED BY public.role_definitions.id;

CREATE TABLE public.semantic_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_bot_id uuid NOT NULL,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    bot_version text DEFAULT 'latest'::text NOT NULL,
    corpus_version text DEFAULT 'latest'::text NOT NULL,
    query_hash character(64) NOT NULL,
    answer text NOT NULL,
    citations jsonb DEFAULT '[]'::jsonb NOT NULL,
    model_name text DEFAULT ''::text NOT NULL,
    cached_at_ts bigint DEFAULT 0 NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    query_embedding public.vector(1024),
    CONSTRAINT semantic_cache_workspace_id_format_check CHECK (((length((workspace_id)::text) >= 1) AND (length((workspace_id)::text) <= 64) AND ((workspace_id)::text ~ '^[a-zA-Z0-9-]+$'::text)))
);

ALTER TABLE ONLY public.semantic_cache FORCE ROW LEVEL SECURITY;

CREATE TABLE public.system_config (
    key character varying(128) NOT NULL,
    value jsonb NOT NULL,
    value_type character varying(32) DEFAULT 'string'::character varying NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.tenant_model_policy (
    id uuid NOT NULL,
    record_tenant_id uuid,
    workspace_id character varying(64) NOT NULL,
    channel_type character varying(64),
    record_bot_id uuid,
    record_model_id uuid NOT NULL,
    private_doc_ratio integer NOT NULL,
    web_search_ratio integer NOT NULL,
    general_knowledge_ratio integer NOT NULL,
    record_fallback_model_id uuid,
    default_for_task jsonb NOT NULL,
    enabled boolean NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(128),
    updated_by character varying(128),
    CONSTRAINT ck_policy_ratio_sum CHECK ((((private_doc_ratio + web_search_ratio) + general_knowledge_ratio) = 100))
);

ALTER TABLE ONLY public.tenant_model_policy FORCE ROW LEVEL SECURITY;

CREATE TABLE public.tenant_webhook_secrets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_tenant_id uuid NOT NULL,
    webhook_id uuid NOT NULL,
    version integer NOT NULL,
    secret_hash character varying(128) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    grace_period_hours integer DEFAULT 24 NOT NULL
);

CREATE TABLE public.tenant_webhooks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_tenant_id uuid NOT NULL,
    url character varying(2048) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone
);

CREATE TABLE public.tenants (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    quota_monthly_tokens integer NOT NULL,
    callback_url text,
    callback_hmac_secret text,
    config jsonb NOT NULL,
    bypass_rate_limit boolean NOT NULL,
    rate_limit_per_min integer,
    monthly_token_cap integer,
    allowed_origins jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_tenants_monthly_token_cap_nonneg CHECK (((monthly_token_cap IS NULL) OR (monthly_token_cap >= 0))),
    CONSTRAINT ck_tenants_rate_limit_per_min_nonneg CHECK (((rate_limit_per_min IS NULL) OR (rate_limit_per_min >= 0)))
);

CREATE TABLE public.token_budgets (
    id bigint NOT NULL,
    record_tenant_id uuid NOT NULL,
    workspace_id character varying(64),
    record_bot_id uuid,
    budget_level character varying(16) NOT NULL,
    period_type character varying(16) NOT NULL,
    token_limit bigint NOT NULL,
    cost_limit_usd numeric(12,4),
    alert_at_pct integer DEFAULT 80 NOT NULL,
    hard_cap boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_token_budgets_level CHECK (((budget_level)::text = ANY (ARRAY[('tenant'::character varying)::text, ('workspace'::character varying)::text, ('bot'::character varying)::text]))),
    CONSTRAINT ck_token_budgets_period CHECK (((period_type)::text = ANY (ARRAY[('daily'::character varying)::text, ('monthly'::character varying)::text])))
);

ALTER TABLE public.token_budgets ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.token_budgets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.token_ledger (
    id bigint NOT NULL,
    mode character varying(16) NOT NULL,
    action character varying(32) NOT NULL,
    purpose character varying(64),
    provider character varying(64),
    model character varying(128),
    record_tenant_id uuid,
    record_bot_id uuid,
    bot_id character varying(255),
    workspace_id character varying(64),
    channel_type character varying(32),
    request_id uuid,
    document_id uuid,
    trace_id character varying(128),
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    cached_tokens integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    duration_ms integer,
    input_unit_price numeric(12,6),
    output_unit_price numeric(12,6),
    cached_unit_price numeric(12,6),
    cost_usd numeric(14,8),
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    finish_reason character varying(32),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.token_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.token_ledger_id_seq OWNED BY public.token_ledger.id;

CREATE TABLE public.workspaces (
    id uuid NOT NULL,
    record_tenant_id uuid NOT NULL,
    slug character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.chat_histories ALTER COLUMN id SET DEFAULT nextval('public.chat_histories_id_seq'::regclass);

ALTER TABLE ONLY public.module_permissions ALTER COLUMN id SET DEFAULT nextval('public.module_permissions_id_seq'::regclass);

ALTER TABLE ONLY public.monitoring_log ALTER COLUMN id SET DEFAULT nextval('public.monitoring_log_id_seq'::regclass);

ALTER TABLE ONLY public.role_definitions ALTER COLUMN id SET DEFAULT nextval('public.role_definitions_id_seq'::regclass);

ALTER TABLE ONLY public.token_ledger ALTER COLUMN id SET DEFAULT nextval('public.token_ledger_id_seq'::regclass);

ALTER TABLE ONLY public.ai_keys
    ADD CONSTRAINT ai_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ai_providers
    ADD CONSTRAINT ai_providers_name_key UNIQUE (name);

ALTER TABLE ONLY public.ai_providers
    ADD CONSTRAINT ai_providers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_service_name_key UNIQUE (service_name);

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bot_model_bindings
    ADD CONSTRAINT bot_model_bindings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bot_token_usage_log
    ADD CONSTRAINT bot_token_usage_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bots
    ADD CONSTRAINT bots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.chat_histories
    ADD CONSTRAINT chat_histories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.document_service_index
    ADD CONSTRAINT document_service_index_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.guardrail_events
    ADD CONSTRAINT guardrail_events_pkey PRIMARY KEY (event_id);

ALTER TABLE ONLY public.guardrail_rules
    ADD CONSTRAINT guardrail_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ingest_idempotency_keys
    ADD CONSTRAINT ingest_idempotency_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_edges
    ADD CONSTRAINT knowledge_edges_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_capabilities
    ADD CONSTRAINT model_capabilities_pkey PRIMARY KEY (record_model_id);

ALTER TABLE ONLY public.model_invocations
    ADD CONSTRAINT model_invocations_pkey PRIMARY KEY (invocation_id);

ALTER TABLE ONLY public.module_permissions
    ADD CONSTRAINT module_permissions_module_permission_key UNIQUE (module, permission);

ALTER TABLE ONLY public.module_permissions
    ADD CONSTRAINT module_permissions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.monitoring_log
    ADD CONSTRAINT monitoring_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.event_inbox
    ADD CONSTRAINT pk_event_inbox PRIMARY KEY (subscriber_id, msg_id);

ALTER TABLE ONLY public.language_packs
    ADD CONSTRAINT pk_language_packs PRIMARY KEY (code, prompt_key);

ALTER TABLE ONLY public.prompt_templates
    ADD CONSTRAINT prompt_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.prompt_versions
    ADD CONSTRAINT prompt_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.quotas
    ADD CONSTRAINT quotas_pkey PRIMARY KEY (record_tenant_id);

ALTER TABLE ONLY public.refuse_suggestions
    ADD CONSTRAINT refuse_suggestions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.request_chunk_refs
    ADD CONSTRAINT request_chunk_refs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.request_logs
    ADD CONSTRAINT request_logs_pkey PRIMARY KEY (request_id);

ALTER TABLE ONLY public.request_steps
    ADD CONSTRAINT request_steps_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.role_definitions
    ADD CONSTRAINT role_definitions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.role_definitions
    ADD CONSTRAINT role_definitions_role_name_key UNIQUE (role_name);

ALTER TABLE ONLY public.semantic_cache
    ADD CONSTRAINT semantic_cache_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT system_config_pkey PRIMARY KEY (key);

ALTER TABLE ONLY public.tenant_model_policy
    ADD CONSTRAINT tenant_model_policy_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tenant_webhook_secrets
    ADD CONSTRAINT tenant_webhook_secrets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tenant_webhooks
    ADD CONSTRAINT tenant_webhooks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_name_key UNIQUE (name);

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.token_budgets
    ADD CONSTRAINT token_budgets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.token_ledger
    ADD CONSTRAINT token_ledger_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT uq_ai_model_provider_name UNIQUE (record_provider_id, name);

ALTER TABLE ONLY public.bot_model_bindings
    ADD CONSTRAINT uq_binding_unique UNIQUE (record_tenant_id, record_bot_id, purpose, rank, variant);

ALTER TABLE ONLY public.bots
    ADD CONSTRAINT uq_bots_record_tenant_workspace_bot_channel UNIQUE (record_tenant_id, workspace_id, bot_id, channel_type);

ALTER TABLE ONLY public.bot_token_usage_log
    ADD CONSTRAINT uq_btul_record_tenant_workspace_bot_channel UNIQUE (record_tenant_id, workspace_id, bot_id, channel_type);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT uq_conv_bot_connect UNIQUE (record_bot_id, connect_id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_doc_tool UNIQUE (record_tenant_id, record_bot_id, tool_name);

ALTER TABLE ONLY public.ingest_idempotency_keys
    ADD CONSTRAINT uq_ingest_idemkey UNIQUE (record_tenant_id, workspace_id, idempotency_key);

ALTER TABLE ONLY public.prompt_templates
    ADD CONSTRAINT uq_prompt_unique UNIQUE (record_tenant_id, record_bot_id, template_key, version);

ALTER TABLE ONLY public.prompt_versions
    ADD CONSTRAINT uq_prompt_versions_tenant_name_ver UNIQUE (record_tenant_id, name, version_no);

ALTER TABLE ONLY public.refuse_suggestions
    ADD CONSTRAINT uq_refuse_suggestions_bot_intent UNIQUE (record_bot_id, query_intent);

ALTER TABLE ONLY public.tenant_model_policy
    ADD CONSTRAINT uq_tenant_policy UNIQUE (record_tenant_id, record_bot_id, record_model_id);

ALTER TABLE ONLY public.tenant_webhook_secrets
    ADD CONSTRAINT uq_tenant_webhook_secrets_version UNIQUE (record_tenant_id, webhook_id, version);

ALTER TABLE ONLY public.token_budgets
    ADD CONSTRAINT uq_token_budgets_scope UNIQUE (record_tenant_id, workspace_id, record_bot_id, budget_level, period_type);

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT uq_workspaces_tenant_slug UNIQUE (record_tenant_id, slug);

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (id);

CREATE INDEX idx_chunks_parent ON public.document_chunks USING btree (parent_chunk_id) WHERE (parent_chunk_id IS NOT NULL);

CREATE INDEX idx_chunks_search_vector ON public.document_chunks USING gin (search_vector);

CREATE INDEX idx_chunks_search_vector_combined ON public.document_chunks USING gin (to_tsvector('simple'::regconfig, ((COALESCE(content, ''::text) || ' '::text) || COALESCE(chunk_context, ''::text)))) WHERE (chunk_context IS NOT NULL);

CREATE INDEX idx_documents_access_groups ON public.documents USING gin (access_groups);

CREATE INDEX idx_documents_summary_json ON public.documents USING gin (summary_json) WHERE (summary_json IS NOT NULL);

CREATE INDEX idx_dsi_attrs ON public.document_service_index USING gin (attributes_json);

CREATE INDEX idx_dsi_bot_price1 ON public.document_service_index USING btree (record_bot_id, price_primary);

CREATE INDEX idx_dsi_bot_price2 ON public.document_service_index USING btree (record_bot_id, price_secondary);

CREATE INDEX idx_dsi_doc ON public.document_service_index USING btree (record_document_id);

CREATE INDEX idx_knowledge_edges_bot_channel ON public.knowledge_edges USING btree (record_bot_id, channel_type);

CREATE INDEX idx_knowledge_edges_object ON public.knowledge_edges USING btree (record_bot_id, channel_type, object);

CREATE INDEX idx_knowledge_edges_subject ON public.knowledge_edges USING btree (record_bot_id, channel_type, subject);

CREATE UNIQUE INDEX idx_knowledge_edges_unique ON public.knowledge_edges USING btree (record_bot_id, channel_type, subject, relation, object);

CREATE INDEX ix_ai_keys_provider_active ON public.ai_keys USING btree (record_provider_id, status) WHERE ((status)::text = 'active'::text);

CREATE INDEX ix_ai_model_kind ON public.ai_models USING btree (kind);

CREATE INDEX ix_api_keys_active ON public.api_keys USING btree (provider_code, active) WHERE ((active = true) AND (deleted_at IS NULL));

CREATE INDEX ix_api_tokens_service ON public.api_tokens USING btree (service_name) WHERE (revoked_at IS NULL);

CREATE INDEX ix_audit_log_chain ON public.audit_log USING btree (created_at, id);

CREATE INDEX ix_audit_log_resource ON public.audit_log USING btree (resource_type, resource_id, created_at);

CREATE INDEX ix_audit_log_tenant_time ON public.audit_log USING btree (record_tenant_id, resource_type, created_at);

CREATE INDEX ix_binding_bot_cheap_purpose ON public.bot_model_bindings USING btree (record_bot_id, purpose) WHERE (((purpose)::text = ANY ((ARRAY['llm_factoid'::character varying, 'llm_chitchat'::character varying, 'llm_oos'::character varying, 'llm_intent_understand'::character varying])::text[])) AND (active = true));

CREATE INDEX ix_binding_bot_purpose ON public.bot_model_bindings USING btree (record_bot_id, purpose, active);

CREATE INDEX ix_bindings_fallback_model_id ON public.bot_model_bindings USING btree (record_fallback_model_id) WHERE (record_fallback_model_id IS NOT NULL);

CREATE INDEX ix_bmb_record_model_id ON public.bot_model_bindings USING btree (record_model_id);

CREATE INDEX ix_bots_metadata_extraction_config_gin ON public.bots USING gin (metadata_extraction_config) WHERE (metadata_extraction_config IS NOT NULL);

CREATE INDEX ix_bots_model ON public.bots USING btree (record_model_id);

CREATE INDEX ix_bots_record_tenant_bot_channel ON public.bots USING btree (record_tenant_id, bot_id, channel_type);

CREATE INDEX ix_bots_tokens_used ON public.bots USING btree (tokens_used);

CREATE INDEX ix_btul_record_bot_id ON public.bot_token_usage_log USING btree (record_bot_id);

CREATE INDEX ix_btul_record_tenant_workspace ON public.bot_token_usage_log USING btree (record_tenant_id, workspace_id);

CREATE INDEX ix_chat_histories_bot ON public.chat_histories USING btree (record_bot_id, created_at DESC);

CREATE INDEX ix_chat_histories_room ON public.chat_histories USING btree (record_bot_id, channel_type, connect_id, created_at DESC);

CREATE INDEX ix_chunks_bot_active ON public.document_chunks USING btree (record_bot_id) WHERE (doc_deleted_at IS NULL);

CREATE INDEX ix_chunks_content_hash ON public.document_chunks USING btree (content_hash);

CREATE INDEX ix_chunks_document ON public.document_chunks USING btree (record_document_id);

CREATE INDEX ix_chunks_embedding_hnsw ON public.document_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='32', ef_construction='200');

CREATE INDEX ix_conv_last_message_at ON public.conversations USING btree (last_message_at);

CREATE INDEX ix_conv_tenant ON public.conversations USING btree (record_tenant_id);

CREATE INDEX ix_conversations_bot_user_created ON public.conversations USING btree (record_tenant_id, record_bot_id, connect_id, created_at DESC);

CREATE INDEX ix_doc_bot ON public.documents USING btree (record_bot_id);

CREATE INDEX ix_doc_bot_state ON public.documents USING btree (record_bot_id, state);

CREATE INDEX ix_doc_created ON public.documents USING btree (created_at);

CREATE INDEX ix_doc_state ON public.documents USING btree (state);

CREATE INDEX ix_document_chunks_metadata_json_gin ON public.document_chunks USING gin (metadata_json jsonb_path_ops);

CREATE INDEX ix_document_chunks_record_bot_id ON public.document_chunks USING btree (record_bot_id);

CREATE INDEX ix_document_chunks_record_document_id ON public.document_chunks USING btree (record_document_id);

CREATE INDEX ix_documents_bot_source ON public.documents USING btree (record_bot_id, source_url) WHERE (deleted_at IS NULL);

CREATE INDEX ix_documents_metadata_json_gin ON public.documents USING gin (metadata_json);

CREATE INDEX ix_documents_tenant_deleted ON public.documents USING btree (record_tenant_id, deleted_at DESC);

CREATE INDEX ix_event_inbox_processed_at ON public.event_inbox USING btree (processed_at);

CREATE INDEX ix_guardrail_events_message ON public.guardrail_events USING btree (message_id);

CREATE INDEX ix_guardrail_events_rule_severity ON public.guardrail_events USING btree (rule_id, severity);

CREATE INDEX ix_guardrail_events_tenant_time ON public.guardrail_events USING btree (record_tenant_id, detected_at);

CREATE INDEX ix_guardrail_rules_tenant_scope_enabled ON public.guardrail_rules USING btree (record_tenant_id, scope);

CREATE INDEX ix_ingest_idemkey_expires ON public.ingest_idempotency_keys USING btree (expires_at);

CREATE INDEX ix_jobs_created ON public.jobs USING btree (created_at);

CREATE INDEX ix_jobs_tenant_status ON public.jobs USING btree (record_tenant_id, status);

CREATE INDEX ix_messages_record_bot_id ON public.messages USING btree (record_bot_id);

CREATE INDEX ix_model_inv_feature_started ON public.model_invocations USING btree (feature_name, started_at);

CREATE INDEX ix_model_inv_message ON public.model_invocations USING btree (message_id);

CREATE INDEX ix_model_inv_request_attempt ON public.model_invocations USING btree (record_request_id, attempt_no);

CREATE INDEX ix_model_inv_tenant_started ON public.model_invocations USING btree (record_tenant_id, started_at);

CREATE INDEX ix_module_perm_module ON public.module_permissions USING btree (module);

CREATE INDEX ix_monitoring_log_bot_started ON public.monitoring_log USING btree (record_bot_id, started_at);

CREATE INDEX ix_monitoring_log_started ON public.monitoring_log USING btree (started_at);

CREATE INDEX ix_msg_conv_created ON public.messages USING btree (record_conversation_id, created_at);

CREATE INDEX ix_msg_tenant_bot ON public.messages USING btree (record_tenant_id, record_bot_id);

CREATE INDEX ix_outbox_pending ON public.outbox USING btree (processed_at, created_at);

CREATE INDEX ix_outbox_pending_retry ON public.outbox USING btree (status, created_at) WHERE ((status)::text = ANY ((ARRAY['pending'::character varying, 'retry'::character varying])::text[]));

CREATE INDEX ix_outbox_subject ON public.outbox USING btree (subject);

CREATE INDEX ix_prompt_active ON public.prompt_templates USING btree (record_tenant_id, record_bot_id, template_key, active);

CREATE INDEX ix_prompt_versions_purpose ON public.prompt_versions USING btree (purpose);

CREATE INDEX ix_rcr_chunk ON public.request_chunk_refs USING btree (record_chunk_id);

CREATE INDEX ix_rcr_request ON public.request_chunk_refs USING btree (record_request_id);

CREATE INDEX ix_refuse_suggestions_tenant_bot ON public.refuse_suggestions USING btree (record_tenant_id, record_bot_id);

CREATE INDEX ix_reqlog_conversation ON public.request_logs USING btree (record_conversation_id);

CREATE INDEX ix_reqlog_model ON public.request_logs USING btree (record_model_id);

CREATE INDEX ix_reqlog_question_hash ON public.request_logs USING btree (question_hash);

CREATE INDEX ix_reqlog_status ON public.request_logs USING btree (status);

CREATE INDEX ix_reqlog_tenant_message ON public.request_logs USING btree (record_tenant_id, message_id);

CREATE INDEX ix_reqlog_tenant_started ON public.request_logs USING btree (record_tenant_id, started_at);

CREATE INDEX ix_reqstep_request_order ON public.request_steps USING btree (record_request_id, step_order);

CREATE INDEX ix_reqstep_step_name ON public.request_steps USING btree (step_name);

CREATE INDEX ix_role_def_level ON public.role_definitions USING btree (level);

CREATE INDEX ix_sem_cache_bot ON public.semantic_cache USING btree (record_bot_id, query_hash);

CREATE INDEX ix_semantic_cache_qe_hnsw ON public.semantic_cache USING hnsw (query_embedding public.vector_cosine_ops) WITH (m='32', ef_construction='200');

CREATE INDEX ix_semantic_cache_versions ON public.semantic_cache USING btree (record_bot_id, bot_version, corpus_version);

CREATE INDEX ix_semantic_cache_ws ON public.semantic_cache USING btree (record_bot_id, workspace_id);

CREATE INDEX ix_system_config_updated ON public.system_config USING btree (updated_at DESC);

CREATE INDEX ix_tenant_webhook_secrets_lookup ON public.tenant_webhook_secrets USING btree (record_tenant_id, webhook_id, version);

CREATE INDEX ix_tenant_webhooks_tenant ON public.tenant_webhooks USING btree (record_tenant_id);

CREATE INDEX ix_tmp_record_bot_id ON public.tenant_model_policy USING btree (record_bot_id);

CREATE INDEX ix_tmp_record_model_id ON public.tenant_model_policy USING btree (record_model_id);

CREATE INDEX ix_token_budgets_tenant_active ON public.token_budgets USING btree (record_tenant_id, is_active);

CREATE INDEX ix_token_ledger_bot_started ON public.token_ledger USING btree (record_bot_id, started_at);

CREATE INDEX ix_token_ledger_mode_started ON public.token_ledger USING btree (mode, started_at);

CREATE INDEX ix_token_ledger_provider ON public.token_ledger USING btree (provider, started_at);

CREATE INDEX ix_token_ledger_started ON public.token_ledger USING btree (started_at);

CREATE INDEX ix_token_ledger_tenant_started ON public.token_ledger USING btree (record_tenant_id, started_at);

CREATE UNIQUE INDEX uq_ai_keys_provider_default ON public.ai_keys USING btree (record_provider_id) WHERE (is_default = true);

CREATE UNIQUE INDEX uq_api_keys_provider_label_live ON public.api_keys USING btree (provider_code, label) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX uq_documents_bot_content_hash ON public.documents USING btree (record_bot_id, content_hash) WHERE (deleted_at IS NULL);

CREATE TRIGGER audit_log_immutable_trigger BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION public.audit_log_immutable();

CREATE TRIGGER trg_chunk_search_vector BEFORE INSERT OR UPDATE OF content, content_segmented ON public.document_chunks FOR EACH ROW EXECUTE FUNCTION public.update_chunk_search_vector();

CREATE TRIGGER trg_sync_doc_deleted_at AFTER UPDATE OF deleted_at ON public.documents FOR EACH ROW EXECUTE FUNCTION public.sync_doc_deleted_at_to_chunks();

ALTER TABLE ONLY public.ai_keys
    ADD CONSTRAINT ai_keys_record_provider_id_fkey FOREIGN KEY (record_provider_id) REFERENCES public.ai_providers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_record_provider_id_fkey FOREIGN KEY (record_provider_id) REFERENCES public.ai_providers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_record_provider_id_fkey FOREIGN KEY (record_provider_id) REFERENCES public.ai_providers(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bot_model_bindings
    ADD CONSTRAINT bot_model_bindings_record_bot_id_fkey FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bot_model_bindings
    ADD CONSTRAINT bot_model_bindings_record_model_id_fkey FOREIGN KEY (record_model_id) REFERENCES public.ai_models(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.bots
    ADD CONSTRAINT bots_record_tenant_id_fkey FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_record_bot_id_fkey FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.document_service_index
    ADD CONSTRAINT document_service_index_record_bot_id_fkey FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.document_service_index
    ADD CONSTRAINT document_service_index_record_chunk_id_fkey FOREIGN KEY (record_chunk_id) REFERENCES public.document_chunks(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.document_service_index
    ADD CONSTRAINT document_service_index_record_document_id_fkey FOREIGN KEY (record_document_id) REFERENCES public.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT fk_audit_log_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.bot_model_bindings
    ADD CONSTRAINT fk_bindings_fallback_model FOREIGN KEY (record_fallback_model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bots
    ADD CONSTRAINT fk_bots_embedding_model FOREIGN KEY (record_embedding_model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bots
    ADD CONSTRAINT fk_bots_model FOREIGN KEY (record_model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT fk_chunks_document FOREIGN KEY (record_document_id) REFERENCES public.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT fk_chunks_parent FOREIGN KEY (parent_chunk_id) REFERENCES public.document_chunks(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT fk_conversations_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_documents_bot FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_documents_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.guardrail_events
    ADD CONSTRAINT fk_guardrail_events_request FOREIGN KEY (record_request_id) REFERENCES public.request_logs(request_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.guardrail_events
    ADD CONSTRAINT fk_guardrail_events_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_bot FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.quotas
    ADD CONSTRAINT fk_quotas_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.request_logs
    ADD CONSTRAINT fk_request_logs_bot FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.request_logs
    ADD CONSTRAINT fk_request_logs_model FOREIGN KEY (record_model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.request_logs
    ADD CONSTRAINT fk_request_logs_tenant FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.semantic_cache
    ADD CONSTRAINT fk_semantic_cache_bot FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_edges
    ADD CONSTRAINT knowledge_edges_record_bot_id_fkey FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_record_conversation_id_fkey FOREIGN KEY (record_conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.model_capabilities
    ADD CONSTRAINT model_capabilities_record_model_id_fkey FOREIGN KEY (record_model_id) REFERENCES public.ai_models(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.request_chunk_refs
    ADD CONSTRAINT request_chunk_refs_record_chunk_id_fkey FOREIGN KEY (record_chunk_id) REFERENCES public.document_chunks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.request_chunk_refs
    ADD CONSTRAINT request_chunk_refs_record_request_id_fkey FOREIGN KEY (record_request_id) REFERENCES public.request_logs(request_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.request_steps
    ADD CONSTRAINT request_steps_record_request_id_fkey FOREIGN KEY (record_request_id) REFERENCES public.request_logs(request_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tenant_model_policy
    ADD CONSTRAINT tenant_model_policy_record_bot_id_fkey FOREIGN KEY (record_bot_id) REFERENCES public.bots(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tenant_model_policy
    ADD CONSTRAINT tenant_model_policy_record_fallback_model_id_fkey FOREIGN KEY (record_fallback_model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.tenant_model_policy
    ADD CONSTRAINT tenant_model_policy_record_model_id_fkey FOREIGN KEY (record_model_id) REFERENCES public.ai_models(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tenant_webhook_secrets
    ADD CONSTRAINT tenant_webhook_secrets_record_tenant_id_fkey FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tenant_webhook_secrets
    ADD CONSTRAINT tenant_webhook_secrets_webhook_id_fkey FOREIGN KEY (webhook_id) REFERENCES public.tenant_webhooks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tenant_webhooks
    ADD CONSTRAINT tenant_webhooks_record_tenant_id_fkey FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_record_tenant_id_fkey FOREIGN KEY (record_tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.bot_model_bindings ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.bots ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.document_chunks ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.document_service_index ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.guardrail_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.knowledge_edges ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.model_invocations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.outbox ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.prompt_templates ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.prompt_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.quotas ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.refuse_suggestions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.request_logs ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.request_steps ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.semantic_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON public.audit_log USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.bot_model_bindings USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.bots USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.conversations USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.document_chunks USING ((EXISTS ( SELECT 1
   FROM public.documents p
  WHERE ((p.id = document_chunks.record_document_id) AND (p.record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.documents p
  WHERE ((p.id = document_chunks.record_document_id) AND (p.record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid)))));

CREATE POLICY tenant_isolation ON public.document_service_index USING ((record_tenant_id = (current_setting('app.tenant_id'::text))::uuid)) WITH CHECK ((record_tenant_id = (current_setting('app.tenant_id'::text))::uuid));

CREATE POLICY tenant_isolation ON public.documents USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.guardrail_events USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.jobs USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.knowledge_edges USING ((EXISTS ( SELECT 1
   FROM public.bots p
  WHERE ((p.id = knowledge_edges.record_bot_id) AND (p.record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.bots p
  WHERE ((p.id = knowledge_edges.record_bot_id) AND (p.record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid)))));

CREATE POLICY tenant_isolation ON public.messages USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.model_invocations USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.outbox USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.prompt_templates USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.prompt_versions USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.quotas USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.refuse_suggestions USING ((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid)) WITH CHECK ((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid));

CREATE POLICY tenant_isolation ON public.request_logs USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.request_steps USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.semantic_cache USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

CREATE POLICY tenant_isolation ON public.tenant_model_policy USING (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true))))) WITH CHECK (((record_tenant_id = (current_setting('app.tenant_id'::text, true))::uuid) AND ((COALESCE(current_setting('app.workspace_id'::text, true), ''::text) = ''::text) OR ((workspace_id)::text = current_setting('app.workspace_id'::text, true)))));

ALTER TABLE public.tenant_model_policy ENABLE ROW LEVEL SECURITY;

