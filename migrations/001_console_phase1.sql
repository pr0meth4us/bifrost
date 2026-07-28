-- Ministry Exam Prep — Admin Console, Phase 1 schema additions
-- Run against the tenant Supabase database (staging first).
--
-- Per SOW §2, these are PROPOSED, not applied unilaterally. The console degrades
-- gracefully without them — it introspects the schema and skips what isn't there —
-- but two acceptance criteria cannot be met until they land:
--   * refund revoking the correct exam track      -> payments.exam_track_id
--   * duplicate txn_ref rejection under concurrency -> payments.txn_ref UNIQUE
--
-- Idempotent: safe to re-run.

BEGIN;

-- 1. Which track a payment unlocked ---------------------------------------
-- Without this, refund has to infer the track from the user's active
-- entitlements and refuses to act when the answer is ambiguous. This is the
-- fix for "defaulted refunds to the wrong exam track".
ALTER TABLE payments ADD COLUMN IF NOT EXISTS exam_track_id INTEGER REFERENCES exam_tracks (id);

-- Backfill for already-approved payments where the user holds exactly one
-- entitlement. Rows that stay NULL are genuinely ambiguous and are left for a
-- human — the console will say so rather than guess.
UPDATE payments p
SET exam_track_id = e.exam_track_id
FROM (
    SELECT user_id, MIN(exam_track_id) AS exam_track_id
    FROM entitlements
    WHERE status = 'premium'
    GROUP BY user_id
    HAVING COUNT(*) = 1
) e
WHERE p.user_id = e.user_id
  AND p.exam_track_id IS NULL
  AND p.status IN ('approved', 'refunded');

-- 2. Fraud controls --------------------------------------------------------
-- txn_ref is documented as unique; enforce it in the database so the guarantee
-- survives two admins clicking Approve at the same moment.
CREATE UNIQUE INDEX IF NOT EXISTS payments_txn_ref_key ON payments (txn_ref);

-- Receipt image fingerprint, written by the uploading app. The console warns
-- when a new receipt matches an existing one.
ALTER TABLE payments ADD COLUMN IF NOT EXISTS receipt_checksum TEXT;
CREATE INDEX IF NOT EXISTS payments_receipt_checksum_idx ON payments (receipt_checksum);

-- 3. Reason codes and timestamps ------------------------------------------
ALTER TABLE payments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE payments ADD COLUMN IF NOT EXISTS reject_reason TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_reason TEXT;

-- 4. Entitlement upsert target --------------------------------------------
-- One entitlement per user per track. Approve relies on this to stay correct
-- when two payments for the same track are approved concurrently.
CREATE UNIQUE INDEX IF NOT EXISTS entitlements_user_track_key
    ON entitlements (user_id, exam_track_id);

-- 5. Account suspension ----------------------------------------------------
-- Suspension is reversible, so it must not touch entitlements. Gate access on
-- users.status in the app.
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspend_reason TEXT;

-- 6. Content provenance ----------------------------------------------------
-- Per SOW §8: keep the pilot question set separable from production.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS question_source TEXT;

COMMIT;

-- 7. Restricted console role (SOW §4.3) ------------------------------------
-- Run as a Supabase owner. The console must NOT hold service_role.
-- Password: generate one, store it in the console's encrypted credential store,
-- and rotate per the policy in docs/console-onboarding.md.
--
--   CREATE ROLE console_agent LOGIN PASSWORD '<generated>';
--   GRANT CONNECT ON DATABASE postgres TO console_agent;
--   GRANT USAGE ON SCHEMA public TO console_agent;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON
--       payments, entitlements, users, exam_tracks, questions, choices,
--       glossary_terms, question_terms, attempts, attempt_answers, decoder_taps
--       TO console_agent;
--   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO console_agent;
--   -- explicit denials
--   REVOKE CREATE ON SCHEMA public FROM console_agent;
--   REVOKE ALL ON SCHEMA auth FROM console_agent;
--   REVOKE ALL ON ALL TABLES IN SCHEMA auth FROM console_agent;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM console_agent;
--
-- Verify with:  \du console_agent   and   SET ROLE console_agent; CREATE TABLE t(i int);
-- The CREATE TABLE must fail.
