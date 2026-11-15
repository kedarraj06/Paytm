# Paytm Autonomous UPI Dispute Swarm — Project Idea

## 1. The problem

India completes billions of UPI payments every month. A small share of them go wrong: the customer's bank account is debited, but the other side never receives the money, the merchant never gets a confirmation, or the Paytm app shows "failed" even though the payment went through.

When this happens the customer has no idea where their money is. Today the usual path looks like this:

- The customer contacts support and explains the problem, often several times.
- A human agent copies reference numbers between internal tools and checks several systems by hand. This takes minutes per case.
- Nobody tells the customer which institution is actually responsible, so they get bounced between the app, their bank and the merchant.
- Deadlines exist (RBI sets turnaround times for reversals), but cases get forgotten and nobody tracks them.

Most disputed amounts are tiny, often ₹20 to ₹100. Handling each one with a human costs about as much as the amount in dispute. That is expensive for the company and slow and frustrating for the customer.

## 2. The idea

Build an **autonomous dispute investigator**, not a support chatbot.

A chatbot answers "your payment is being processed". This system does the work behind the answer. It investigates what happened to the money, works out who must act next, files the dispute with the right institution, watches the deadline, and keeps everyone informed until the case closes.

The product answers three questions for every complaint:

1. **What happened to my money?**
2. **Which institution needs to take the next action?**
3. **What should happen next, and by when?**

## 3. How a case flows (the story of one complaint)

A customer writes: *"I sent ₹500 to a friend. My bank account was debited but he didn't receive it."*

1. **Understand.** The system creates a case, picks out the amount and transaction, and records what the customer *claims*. It treats the claim as a claim, not as a fact.
2. **Investigate.** It checks every party that touched the payment: the Paytm record, the customer's bank, the receiver's bank, the payment service provider, the NPCI network, the merchant or acquirer, and any reversal. It lines up their answers in time order and finds the last point where the money is *confirmed* to be. Missing information is never assumed to mean "failed". Each fact is marked confirmed, not confirmed, pending or unknown.
3. **Decide who acts.** From the evidence and a configurable rule table, it decides the category of the problem and the institution responsible for the next step. It does not send everything to NPCI or RBI.
4. **Check the action.** A strict, non-AI policy engine approves or blocks what the agents want to do.
5. **File the complaint.** If approved, it prepares a structured complaint with the evidence and submits it to the correct channel only.
6. **Track the deadline.** It applies the right reversal timeline (T+1 for "debited, beneficiary not credited", T+5 for "debited, merchant confirmation missing") and keeps watching in the background, whether or not anyone opens the app.
7. **Keep people informed.** The customer, and where appropriate the merchant, get clear updates in plain language.
8. **Close on evidence.** The case closes only when the evidence shows the money was reversed or the status was corrected.

## 4. The six agents and what each is responsible for

| Agent | Role in plain words |
|---|---|
| **Complaint Router** | The front desk. Reads the complaint, asks for missing details, opens a case, separates what the customer says from what is verified. |
| **Transaction Investigator** | The detective. The most important agent. Reconstructs the payment's life across all institutions and finds the last confirmed state of the money. |
| **Resolution & Routing** | The dispatcher. Decides what kind of failure it is and which institution must act, using rules rather than guesswork. |
| **Complaint Management** | The case officer. Prepares and files the complaint, records the reference number, tracks replies, sends follow-ups, avoids duplicates. |
| **SLA & Escalation** | The clock-watcher. Tracks reversal deadlines and complaint-response windows separately, sends reminders, and escalates only along the proper grievance ladder. |
| **Customer & Merchant Communication** | The voice. Explains status clearly, never promises outcomes, never leaks customer data to merchants. |

They are not six separate chatbots. They are one coordinated workflow that shares a single case record, so each step builds on the last and a case can pause and resume when a bank replies.

## 5. Safety principles (what makes it trustworthy)

- **AI recommends, rules decide.** A deterministic policy engine checks every action. Agents can investigate and prepare, but they can never move money, issue refunds, block accounts or do anything irreversible.
- **Evidence over assumption.** No refund without a confirmed debit. No second payment if the receiver was already credited. Conflicting or missing records go to a human.
- **No duplicate actions.** The same complaint is never filed twice.
- **No premature escalation.** An expired timer alone does not send a case to the RBI. Escalation follows the proper steps and eligibility rules, and the final step needs a human.
- **Privacy by design.** Merchants never see the customer's name or bank, and no fraud labels are shared with other parties.
- **Honest messaging.** The system never claims funds were received or refunded without authoritative confirmation.
- **Full audit trail.** Every decision and external action is recorded and labelled as an AI recommendation, verified evidence, simulated response, approved action or pending human approval.

## 6. The three core situations

1. **Failed fund transfer (₹500).** Sender is debited, receiver is not credited, no reversal yet. The system finds the debit, routes to the bank/PSP dispute route, files a complaint and tracks the T+1 timeline.
2. **Failed merchant payment (₹1,000).** Customer is debited, merchant confirmation is missing. The system rebuilds the timeline, raises a dispute through the right channel and tracks T+5.
3. **App says failed, merchant was paid (₹750).** The customer was debited and the merchant was credited, but Paytm shows "failed". This is a status mismatch, not a refund case. The system requests reconciliation and tells the customer the payment was received. No refund is issued.

Edge cases are also handled: a payment that was never debited (no refund, no complaint), and conflicting records between institutions (sent to a human before anything is filed).

## 7. Where customers and teams meet the system

- **Dashboard.** A control room for the team, with:
  - a live **agent workflow view**, showing the agents as connected blocks that light up as they work
  - an overview with case counts, open and resolved cases, SLA breaches and average investigation time
  - a case investigation page with timeline, evidence from each institution, last confirmed state and responsible institution
  - complaint tracking
  - SLA monitoring with deadlines and potential delay compensation
  - an agent activity log
- **WhatsApp.** Customers already live on WhatsApp, so a Chrome extension captures their messages and turns dispute-like messages into cases automatically. Replies appear in the dashboard. The system recognises follow-ups ("any update?") and does not open duplicate cases.
- **Knowledge graph.** Users, transactions, banks, merchants, cases and complaints are stored as a graph, so relationships between cases, parties and institutions can be explored.

## 8. Why it matters

- **For customers:** a clear answer, a named responsible institution, a deadline, and updates without chasing anyone.
- **For Paytm and banks:** lower cost per dispute, faster resolution, fewer forgotten cases, and a complete audit record for compliance.
- **For the ecosystem:** consistent, evidence-based handling that follows regulatory timelines instead of ad hoc support.

## 9. Scope of this prototype

This is a working hackathon prototype. Paytm, the banks, the PSP and NPCI are **simulated**, and the integration points are kept modular so authorised real APIs can be connected later. The data uses three demo customers: Shiva Singh, Tejas Satish Kadam and Kedarraj Bhimrao Birader.

What is real in the prototype: the multi-agent workflow, the policy engine, deadline tracking, case persistence, the audit trail, the dashboard, and the WhatsApp capture path. What is simulated: the institutions' responses, the complaint submissions, and the replies sent back to customers (they appear in the dashboard but are not actually sent on WhatsApp).

## 10. Where it could go next

- Connect authorised real Paytm, bank and NPCI APIs in place of the simulators.
- Actually send replies back through WhatsApp, SMS and email.
- Add more dispute categories and the full RBI grievance ladder with real escalation paths.
- Learn from resolved cases to spot institutions that are slow or unreliable.
- Support regional languages and Hinglish for customer messages.
