CREATE TABLE IF NOT EXISTS comparisons (
    id text PRIMARY KEY,
    content_sha256 char(64) NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    mode text NOT NULL CHECK (mode IN ('fixture','live')),
    verdict text NOT NULL CHECK (verdict IN ('PASS','FAIL','INCONCLUSIVE')),
    artifact jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS trials (
    comparison_id text NOT NULL REFERENCES comparisons (id),
    case_id text NOT NULL,
    arm text NOT NULL CHECK (arm IN ('baseline','candidate')),
    trial_index integer NOT NULL CHECK (trial_index >= 1),
    status text NOT NULL CHECK (status IN ('ok','invalid','error')),
    passed boolean NOT NULL,
    input_tokens integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    latency_ms double precision CHECK (latency_ms >= 0),
    cost_usd numeric(18,10) CHECK (cost_usd >= 0),
    PRIMARY KEY (comparison_id, case_id, arm, trial_index)
);

CREATE TABLE IF NOT EXISTS assertions (
    comparison_id text NOT NULL,
    case_id text NOT NULL,
    arm text NOT NULL,
    trial_index integer NOT NULL,
    name text NOT NULL,
    passed boolean NOT NULL,
    detail text NOT NULL,
    PRIMARY KEY (comparison_id, case_id, arm, trial_index, name),
    FOREIGN KEY (comparison_id, case_id, arm, trial_index)
        REFERENCES trials (comparison_id, case_id, arm, trial_index)
);

CREATE OR REPLACE VIEW arm_usage AS
SELECT comparison_id, arm, count(*) AS scheduled_trials,
       count(*) FILTER (WHERE status != 'error') AS delivered_trials,
       count(*) FILTER (WHERE passed) AS passing_trials,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
           FILTER (WHERE status != 'error') AS p95_ms,
       sum(cost_usd) AS known_cost_usd,
       count(*) FILTER (WHERE cost_usd IS NULL) AS unknown_cost_trials
FROM trials GROUP BY comparison_id, arm;
