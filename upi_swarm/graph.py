"""One coordinated, stateful LangGraph workflow. State persists in upi_disputes.state so a case resumes when new bank responses arrive."""
import operator
from typing import Annotated, TypedDict, Any
from langgraph.graph import StateGraph, START, END
import store
import agents as A


class CaseState(TypedDict, total=False):
    case_id: str; description: str; transaction_id: str; user_id: str; mode: str
    sim_days: float; human_approved: bool; needs_info: bool; missing_information: list; human_review: bool
    router: dict; investigation: dict; resolution: dict; policy: dict; complaint: dict; followup: Any
    sla: dict; sla_origin: Any; wa_chat: str; comms: dict; status: str; reminders_sent: list; escalation_level: Any
    trace: Annotated[list, operator.add]


def build():
    g = StateGraph(CaseState)
    for name, fn in [("router", A.router_agent), ("request_info", A.request_info_node), ("investigator", A.investigator_agent),
                     ("resolution", A.resolution_agent), ("policy", A.policy_node), ("human_review", A.human_review_node),
                     ("complaint_mgmt", A.complaint_agent), ("sla", A.sla_agent), ("comms", A.comms_agent), ("monitor", A.monitor_node)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, lambda s: "investigator" if s.get("mode") == "followup" else "router", {"investigator": "investigator", "router": "router"})
    g.add_conditional_edges("router", lambda s: "request_info" if s.get("needs_info") else "investigator", {"request_info": "request_info", "investigator": "investigator"})
    g.add_edge("request_info", "monitor")
    g.add_edge("investigator", "resolution")
    g.add_edge("resolution", "policy")
    g.add_conditional_edges("policy", lambda s: {"APPROVED": "complaint_mgmt", "ALREADY_SUBMITTED": "complaint_mgmt", "HUMAN_REVIEW": "human_review"}.get(s["policy"]["decision"], "sla"),
                            {"complaint_mgmt": "complaint_mgmt", "human_review": "human_review", "sla": "sla"})
    g.add_edge("human_review", "sla")
    g.add_edge("complaint_mgmt", "sla")
    g.add_edge("sla", "comms")
    g.add_edge("comms", "monitor")
    g.add_edge("monitor", END)
    return g.compile()


WORKFLOW = build()

FLOW_DEF = {
    "nodes": [
        {"id": "router", "title": "1 · Complaint Router", "kind": "agent", "x": 30, "y": 40},
        {"id": "investigator", "title": "2 · Transaction Investigator", "kind": "agent", "x": 300, "y": 40},
        {"id": "resolution", "title": "3 · Resolution & Routing", "kind": "agent", "x": 570, "y": 40},
        {"id": "policy", "title": "Policy Engine (deterministic)", "kind": "policy", "x": 840, "y": 40},
        {"id": "complaint_mgmt", "title": "4 · Complaint Management", "kind": "agent", "x": 840, "y": 250},
        {"id": "sla", "title": "5 · SLA & Escalation", "kind": "agent", "x": 570, "y": 250},
        {"id": "comms", "title": "6 · Customer/Merchant Comms", "kind": "agent", "x": 300, "y": 250},
        {"id": "monitor", "title": "Case Monitoring & Closure", "kind": "system", "x": 30, "y": 250},
        {"id": "request_info", "title": "Request Info from Customer", "kind": "system", "x": 30, "y": 440},
        {"id": "human_review", "title": "Human Review", "kind": "human", "x": 840, "y": 440},
    ],
    "edges": [
        ["router", "investigator", ""], ["router", "request_info", "info missing"], ["request_info", "monitor", ""],
        ["investigator", "resolution", ""], ["resolution", "policy", ""], ["policy", "complaint_mgmt", "approved / tracking"],
        ["policy", "human_review", "needs approval"], ["policy", "sla", "no action"], ["human_review", "sla", ""],
        ["complaint_mgmt", "sla", ""], ["sla", "comms", ""], ["comms", "monitor", ""],
    ],
}


def run_new(description="", user_id=None, transaction_id=None, wa_chat=None):
    init = {"description": description, "user_id": user_id, "transaction_id": transaction_id, "mode": "new", "trace": []}
    if wa_chat:
        init["wa_chat"] = wa_chat
    s = WORKFLOW.invoke(init)
    return s


def run_followup(case_id, add_days=0.0, human_approved=None):
    d = store.get("upi_disputes", case_id)
    if not d:
        raise KeyError(case_id)
    st = dict(d["state"] or {})
    st.pop("trace", None)
    st.update(mode="followup", case_id=case_id, sim_days=(st.get("sim_days", 0) or 0) + add_days, needs_info=False, human_review=False, trace=[])
    if human_approved is not None:
        st["human_approved"] = human_approved
    st.setdefault("transaction_id", d["transaction_id"])
    st.setdefault("user_id", d["user_id"])
    return WORKFLOW.invoke(st)
