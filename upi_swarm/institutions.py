"""Simulated Paytm / bank / PSP / NPCI / merchant services. Same interface can later be backed by authorised real APIs."""
import datetime as dt
from fastapi import APIRouter
import store


def parse_ts(s):
    return dt.datetime.fromisoformat(s)


def elapsed_days(txn, sim_days=0):
    return (dt.datetime.now(dt.timezone.utc) - parse_ts(txn["ts"])).total_seconds() / 86400 + (sim_days or 0)


def _gt(txn_id):
    return store.get("upi_ground_truth", txn_id) or {}


def _res(source, status, detail, **extra):
    return {"source": source, "status": status, "detail": detail, **extra}


class Institutions:
    """Every tool returns {source, status in CONFIRMED|NOT_CONFIRMED|PENDING|UNKNOWN, detail}."""

    def get_paytm_transaction(self, txn_id, elapsed=0):
        t = store.get("upi_transactions", txn_id)
        if not t:
            return _res("PAYTM", "UNKNOWN", "Transaction not found", record=None)
        g = _gt(txn_id)
        shown = t["app_status"]
        if g.get("reconcile_day") is not None and elapsed >= g["reconcile_day"]:
            shown = "SUCCESS"
        return _res("PAYTM", "CONFIRMED", f"Paytm app shows status {shown}", record=t, app_status=shown)

    def check_customer_bank_debit(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        return _res("CUSTOMER_BANK", g.get("customer_debit", "UNKNOWN"), "Customer bank ledger lookup (simulated)")

    def check_beneficiary_bank_credit(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        return _res("BENEFICIARY_BANK", g.get("beneficiary_credit", "UNKNOWN"), "Beneficiary bank credit lookup (simulated)")

    def check_psp_transaction_status(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        s = g.get("psp_status", "UNKNOWN")
        return _res("PSP_BANK", s, f"PSP processing state: {s}")

    def check_npci_transaction_status(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        s = g.get("npci_status", "UNKNOWN")
        mapped = {"SUCCESS": "CONFIRMED", "FAILED": "NOT_CONFIRMED", "TIMEOUT": "PENDING", "PENDING": "PENDING"}.get(s, "UNKNOWN")
        return _res("NPCI", mapped, f"NPCI switch response code state: {s}", raw=s)

    def check_merchant_confirmation(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        return _res("MERCHANT_ACQUIRER", g.get("merchant_confirmation") or "UNKNOWN", "Merchant/acquirer confirmation (simulated)")

    def check_reversal_status(self, txn_id, elapsed=0):
        g = _gt(txn_id)
        rd = g.get("reversal_day")
        if rd is not None and elapsed >= rd:
            return _res("REVERSAL_LEDGER", "CONFIRMED", f"Reversal posted on day {rd}", day=rd)
        return _res("REVERSAL_LEDGER", g.get("reversal_status", "NOT_CONFIRMED"), "No reversal posted yet")

    # ---- complaint channels ----
    def submit_complaint(self, channel, payload):
        prefix = {"bank": "BNK", "psp": "PSP", "npci": "NPCI"}[channel]
        ref = f"{prefix}-{store.uid()[:8].upper()}"
        return {"reference_id": ref, "status": "SUBMITTED", "endpoint": f"/mock/{channel}/complaints" if channel != "npci" else "/mock/npci/disputes"}

    def complaint_status(self, ref, txn_id, elapsed):
        rev = self.check_reversal_status(txn_id, elapsed)
        paytm = self.get_paytm_transaction(txn_id, elapsed)
        if rev["status"] == "CONFIRMED":
            return {"status": "RESOLVED", "message": "Institution confirms reversal has been processed."}
        if paytm.get("app_status") == "SUCCESS" and _gt(txn_id).get("reconcile_day") is not None:
            return {"status": "RESOLVED", "message": "Institution reconciled status; transaction shown as SUCCESS."}
        if elapsed >= 1:
            return {"status": "UNDER_INVESTIGATION", "message": "Institution acknowledged and is investigating; no resolution yet."}
        return {"status": "ACKNOWLEDGED", "message": "Complaint received and logged."}


INST = Institutions()

router = APIRouter(prefix="/mock", tags=["mock-institutions"])


@router.get("/paytm/transactions/{txn_id}")
def m_paytm(txn_id: str, elapsed: float = 0): return INST.get_paytm_transaction(txn_id, elapsed)
@router.get("/bank/customer-debit/{txn_id}")
def m_debit(txn_id: str, elapsed: float = 0): return INST.check_customer_bank_debit(txn_id, elapsed)
@router.get("/bank/beneficiary-credit/{txn_id}")
def m_credit(txn_id: str, elapsed: float = 0): return INST.check_beneficiary_bank_credit(txn_id, elapsed)
@router.get("/psp/status/{txn_id}")
def m_psp(txn_id: str, elapsed: float = 0): return INST.check_psp_transaction_status(txn_id, elapsed)
@router.get("/npci/status/{txn_id}")
def m_npci(txn_id: str, elapsed: float = 0): return INST.check_npci_transaction_status(txn_id, elapsed)
@router.get("/merchant/confirmation/{txn_id}")
def m_merchant(txn_id: str, elapsed: float = 0): return INST.check_merchant_confirmation(txn_id, elapsed)
@router.get("/reversal/{txn_id}")
def m_reversal(txn_id: str, elapsed: float = 0): return INST.check_reversal_status(txn_id, elapsed)


@router.post("/bank/complaints")
def m_bank_c(payload: dict): return INST.submit_complaint("bank", payload)
@router.post("/psp/complaints")
def m_psp_c(payload: dict): return INST.submit_complaint("psp", payload)
@router.post("/npci/disputes")
def m_npci_d(payload: dict): return INST.submit_complaint("npci", payload)


@router.get("/complaints/{ref}")
def m_complaint(ref: str):
    rows = store.find("upi_complaints", external_reference=ref)
    if not rows:
        return {"reference_id": ref, "status": "NOT_FOUND"}
    c = rows[0]
    case = store.get("upi_disputes", c["case_id"]) or {}
    txn = store.get("upi_transactions", case.get("transaction_id")) or {}
    sim = (case.get("state") or {}).get("sim_days", 0)
    return {"reference_id": ref, **INST.complaint_status(ref, txn.get("transaction_id"), elapsed_days(txn, sim) if txn else 0)}
