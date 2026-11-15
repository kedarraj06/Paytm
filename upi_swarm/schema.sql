-- Run once in Supabase -> SQL editor. Uses an upi_ prefix so it never touches other tables in this project.
create table if not exists upi_users (user_id text primary key, name text, phone text, email text, bank text, upi_id text);
create table if not exists upi_transactions (transaction_id text primary key, upi_ref text, sender_user_id text, sender_bank text,
  beneficiary_name text, beneficiary_bank text, merchant_id text, amount numeric, ts timestamptz, status text, app_status text, kind text, note text);
create table if not exists upi_ground_truth (transaction_id text primary key, customer_debit text, beneficiary_credit text, merchant_confirmation text,
  psp_status text, npci_status text, reversal_status text, reversal_day numeric, reconcile_day numeric);
create table if not exists upi_disputes (case_id text primary key, transaction_id text, user_id text, issue_category text, responsible_institution text,
  status text, applicable_sla text, deadline timestamptz, created_at timestamptz, updated_at timestamptz, state jsonb);
create table if not exists upi_evidence (evidence_id text primary key, case_id text, source_institution text, evidence_type text,
  evidence_status text, ts timestamptz, response_payload jsonb);
create table if not exists upi_complaints (complaint_id text primary key, case_id text, institution text, external_reference text,
  submission_status text, response jsonb, submitted_at timestamptz, dedupe_key text);
create table if not exists upi_audit_logs (log_id text primary key, case_id text, agent_name text, action text, input_summary text,
  output_summary text, label text, ts timestamptz);
create table if not exists upi_notifications (notification_id text primary key, case_id text, recipient text, message text,
  delivery_status text, ts timestamptz);
create table if not exists upi_wa_devices (id text primary key, name text, prefix text, key_hash text, status text, created timestamptz, last_seen timestamptz, captured numeric, settings jsonb);
create table if not exists upi_wa_chats (chat_key text primary key, phone text, name text, saved_name numeric, user_id text, device_id text, last_message text, last_direction text, last_seen timestamptz, message_count numeric);
create table if not exists upi_wa_messages (id text primary key, chat_key text, direction text, text text, sender text, ts_raw text, captured timestamptz, processed numeric, case_id text);
create index if not exists upi_audit_case on upi_audit_logs(case_id);
create index if not exists upi_evidence_case on upi_evidence(case_id);
notify pgrst, 'reload schema';
