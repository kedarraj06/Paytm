# Paytm Autonomous UPI Dispute Swarm

Six LangGraph agents + a deterministic policy engine investigate failed/disputed UPI payments, route them, file simulated complaints, track T+1/T+5 SLAs and keep the customer informed (dashboard + WhatsApp).

- `upi_swarm/` — FastAPI backend, agents, policy engine, mock banks/PSP/NPCI, Neo4j + Supabase sync, monochrome dashboard
- `whatsapp_extension/` — Chrome extension that captures WhatsApp Web chats into the workflow

## Run
```
cd upi_swarm
copy .env.example .env      # fill in keys
pip install fastapi uvicorn langgraph neo4j supabase python-dotenv psycopg2-binary
python seed.py              # seed fake users (Shiva Singh, Tejas Satish Kadam, Kedarraj Bhimrao Birader)
python -m uvicorn app:app --port 8800
```
Open http://localhost:8800. Run `schema.sql` in the Supabase SQL editor (or `python apply_schema.py`) once. Tests: `python e2e_test.py`.
