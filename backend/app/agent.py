"""LangGraph finance agent.

Graph:   START -> agent <-> tools
                    |
                    v
                  verify --(ungrounded numbers, 1 retry)--> agent
                    |
                   END

* `agent`  – gpt-4o-mini with tool bindings. It is instructed never to do arithmetic itself.
* `tools`  – deterministic pandas analytics + an exact Decimal calculator.
* `verify` – deterministic "number grounding" guard: every figure in the draft answer must
             match a number produced by a tool (or the user). Otherwise the model is sent
             back once to fix it; if it still fails the answer is returned with a warning flag.

Without an OPENAI_API_KEY the same tools are driven by a keyword router so the product
still answers common questions offline.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import analytics as an
from . import money as cur
from . import planning as pl
from . import plans
from .calc import evaluate
from .categorizer import CATEGORIES
from .config import settings
from .services import UserData

MAX_TOOL_ROUNDS = 8


# ----------------------------------------------------------------------------- tools

def _j(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


def build_tools(user_id: int) -> list[StructuredTool]:
    def data() -> UserData:  # fresh per call so edits made mid-conversation are visible
        return UserData(user_id)

    @tool
    def get_overview() -> str:
        """Balances, current-month spending by category, health score, safe-to-spend and top insights."""
        o = data().overview()
        o["monthly"] = o["monthly"][-3:]
        return _j(o)

    @tool
    def get_monthly_spending(months: int = 6) -> str:
        """Income, spending, net and savings rate for each of the last `months` months (current month is partial)."""
        u = data()
        return _j(an.monthly_summary(u.tx, max(1, min(months, 24)), u.as_of))

    @tool
    def get_category_breakdown(month: str | None = None) -> str:
        """Spending by category for a month 'YYYY-MM' (default current month) with previous-month and 6-month-average comparisons."""
        u = data()
        return _j(an.category_breakdown(u.tx, month, u.as_of))

    @tool
    def search_transactions(query: str | None = None, category: str | None = None,
                            start_date: str | None = None, end_date: str | None = None,
                            min_amount: float | None = None, limit: int = 20) -> str:
        """Find transactions by merchant/description text, category, date range (YYYY-MM-DD) or minimum absolute amount.
        Returns matches (newest first), the total match count and the net spend of ALL matches."""
        u = data()
        return _j(an.search_transactions(u.tx, query, category, start_date, end_date, min_amount, None, min(limit, 50)))

    @tool
    def get_top_merchants(start_date: str | None = None, end_date: str | None = None, limit: int = 10) -> str:
        """Merchants ranked by total spend in an optional date range (YYYY-MM-DD)."""
        return _j(an.top_merchants(data().tx, start_date, end_date, limit))

    @tool
    def get_subscriptions(include_inactive: bool = False) -> str:
        """Detected recurring subscriptions and bills with cadence, last amount, monthly/annual cost and next expected charge."""
        u = data()
        return _j(an.subscriptions_summary(u.tx, u.sub_statuses, u.as_of, include_inactive))

    @tool
    def get_unusual_subscriptions() -> str:
        """Subscriptions needing attention: price increases, duplicate billing, overlapping services, new sign-ups,
        high cost, upcoming annual renewals and ones that stopped charging. Includes potential savings."""
        u = data()
        return _j(an.unusual_subscriptions(u.tx, u.as_of))

    @tool
    def get_anomalies(lookback_days: int = 90) -> str:
        """Unusual transactions: outlier amounts, possible duplicate charges, unrecognised merchants, fees."""
        u = data()
        return _j(an.detect_anomalies(u.tx, u.as_of, lookback_days))

    @tool
    def get_budget_status(month: str | None = None) -> str:
        """The user's budgets vs actual spend for a month 'YYYY-MM' (default current), with projected month-end and status."""
        u = data()
        if not u.budgets:
            return _j({"message": "No budgets set. Use suggest_budgets to propose some."})
        return _j(an.budget_status(u.tx, u.budgets, month, u.as_of))

    @tool
    def suggest_budgets() -> str:
        """Data-driven monthly budget suggestions per category with rationale and a 50/30/20 comparison."""
        u = data()
        return _j(an.suggest_budgets(u.tx, u.budgets, u.as_of))

    @tool
    def get_cashflow_forecast(days: int = 30) -> str:
        """Projected cash balance over the next `days` days from scheduled income, recurring charges and average variable spend."""
        u = data()
        f = an.cashflow_forecast(u.tx, u.accounts, max(7, min(days, 180)), u.as_of)
        f["upcoming_events"] = [{"date": s["date"], **e} for s in f.pop("series") for e in s["events"]][:25]
        return _j(f)

    @tool
    def get_safe_to_spend() -> str:
        """How much discretionary money is left this month after recurring charges still due and a 20% savings target."""
        u = data()
        return _j(an.safe_to_spend(u.tx, as_of=u.as_of))

    @tool
    def get_health_score() -> str:
        """Financial health score (0-100) with component breakdown: savings rate, emergency fund, budgets, subscriptions, stability."""
        u = data()
        return _j(an.health_score(u.tx, u.accounts, u.budgets, u.as_of))

    @tool
    def get_goals() -> str:
        """Savings goals with progress, required monthly contribution and whether the user is on track."""
        u = data()
        return _j(an.goals_progress(u.tx, u.goals, u.as_of))

    @tool
    def simulate_savings(cancel_subscriptions: list[str] | None = None,
                         category_cuts_pct: dict[str, float] | None = None) -> str:
        """What-if scenario. cancel_subscriptions: merchant names to cancel (e.g. ["Hulu","Max"]).
        category_cuts_pct: {category: percent_cut} e.g. {"Dining": 20}. Returns monthly/annual savings and goal impact."""
        u = data()
        return _j(an.simulate_savings(u.tx, u.goals, cancel_subscriptions, category_cuts_pct, u.as_of))

    def locked(u: UserData, feature: str) -> str | None:
        return None if plans.allows(u.plan, feature) else _j({"error": str(plans.PlanRequired(feature, u.plan))})

    @tool
    def get_net_worth() -> str:
        """Net worth: cash accounts + manual assets (investments, retirement, property, vehicles) minus card balances
        and debts, the asset mix, and month-end cash for the last 12 months."""
        u = data()
        return _j(pl.net_worth(u.tx, u.accounts, u.assets, u.debts, u.as_of))

    @tool
    def get_debt_payoff_plan(extra_monthly: float = 0) -> str:
        """Debt payoff plan comparing avalanche (highest APR first), snowball (smallest balance first) and minimum
        payments: debt-free date, total interest, interest saved and payoff order. extra_monthly: dollars paid on top of minimums."""
        u = data()
        if err := locked(u, "debt_planner"):
            return err
        p = pl.debt_payoff_plan(u.debts, int(round(max(0.0, extra_monthly) * 100)), u.as_of, u.tx)
        p.pop("chart", None)
        return _j(p)

    @tool
    def get_bill_calendar(month: str | None = None) -> str:
        """Every recurring bill, subscription and paycheck in a month 'YYYY-MM' (default current): posted and upcoming,
        with totals still due."""
        u = data()
        return _j(pl.bill_calendar(u.tx, month, u.as_of))

    @tool
    def get_challenges() -> str:
        """The user's savings challenges (no-spend days, category caps, merchant breaks) with live progress and
        estimated savings, plus challenges suggested from their habits."""
        u = data()
        if err := locked(u, "challenges"):
            return err
        return _j(pl.challenges_overview(u.challenges, u.tx, u.as_of))

    @tool
    def get_year_in_review(year: int | None = None) -> str:
        """'Money Wrapped' year in review for a calendar year (default: last 12 full months): totals, savings rate,
        spending persona, top merchants, coffee and delivery habits, no-spend days."""
        u = data()
        if err := locked(u, "wrapped"):
            return err
        try:
            return _j(pl.year_in_review(u.tx, u.as_of, year))
        except ValueError as exc:
            return _j({"error": str(exc)})

    @tool
    def can_i_afford(amount: float, date: str | None = None, recurring: bool = False, label: str | None = None) -> str:
        """Check a purchase before it's made. amount: the price in the user's currency. date: 'YYYY-MM-DD' when
        they'd buy it (default today). recurring: true for a monthly cost such as a subscription or EMI.
        Returns a verdict (comfortable | tight | not_now) with reasons, the lowest balance before/after,
        safe-to-spend before/after, goal delays in months and, if it doesn't fit now, the earliest date it would."""
        from datetime import date as _date
        from .afford import AffordError, check
        u = data()
        try:
            when = _date.fromisoformat(date) if date else None
            r = check(u.tx, u.accounts, u.goals, int(round(abs(amount) * 100)), when, recurring, u.as_of, label)
        except (ValueError, AffordError) as exc:
            return _j({"error": str(exc)})
        r.pop("series", None)
        return _j(r)

    @tool
    def calculator(expression: str) -> str:
        """Exact arithmetic for ANY number not returned directly by another tool. Supports + - * / ** ( ),
        round(x, places), abs, min, max, sum(...), avg(...), pct(part, whole), pct_change(old, new).
        Example: "pct_change(15.49, 17.99)" or "round(652.92 * 12, 2)"."""
        try:
            return _j({"expression": expression, "result": evaluate(expression)})
        except ValueError as exc:
            return _j({"error": str(exc)})

    return [get_overview, get_monthly_spending, get_category_breakdown, search_transactions, get_top_merchants,
            get_subscriptions, get_unusual_subscriptions, get_anomalies, get_budget_status, suggest_budgets,
            get_cashflow_forecast, get_safe_to_spend, get_health_score, get_goals, simulate_savings,
            get_net_worth, get_debt_payoff_plan, get_bill_calendar, get_challenges, get_year_in_review, can_i_afford,
            calculator]


# ----------------------------------------------------------------------------- grounding guard

_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
# Longest symbol first so "S$" wins over "$" and the whole marker is consumed.
_SYM_ALT = "|".join(re.escape(s) for s in sorted(cur.SYMBOLS, key=len, reverse=True))
_SYM_RE = re.compile(_SYM_ALT)
# A money figure in any supported currency: ₹12,34,567.89, -$1,234.56, AED 99.00.
_NUM_RE = re.compile(rf"(?<![\w.])(-?(?:{_SYM_ALT})?\s?-?\d[\d,]*(?:\.\d+)?)(%?)")


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """Return (raw, value, decimals) for figures in text that should be verified."""
    out = []
    for m in _NUM_RE.finditer(_DATE_RE.sub(" ", text)):
        raw, is_pct = m.group(1), bool(m.group(2))
        clean = _SYM_RE.sub("", raw).replace(",", "").replace(" ", "")
        try:
            val = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        has_symbol = bool(_SYM_RE.search(raw))
        # plain small integers (counts, days, list numbering) and years are not money claims
        if not (has_symbol or is_pct or decimals) and (abs(val) < 100 or 1900 <= val <= 2100):
            continue
        out.append((raw + ("%" if is_pct else ""), val, decimals))
    return out


def source_numbers(texts: list[str]) -> list[float]:
    nums: list[float] = []
    for t in texts:
        for m in re.finditer(r"-?\d+(?:\.\d+)?", _DATE_RE.sub(" ", t.replace(",", ""))):
            nums.append(float(m.group()))
    return nums


def check_grounding(answer: str, sources: list[float]) -> list[str]:
    abs_sources = [abs(s) for s in sources]
    bad = []
    for raw, val, decimals in extract_numbers(answer):
        tol = 0.5 if decimals == 0 else 0.05 if decimals == 1 else 0.005
        if not any(abs(abs(val) - s) <= tol + 1e-9 for s in abs_sources):
            bad.append(raw)
    return bad


# ----------------------------------------------------------------------------- graph

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    grounding_retries: int
    ungrounded: list[str]


def system_prompt(user: UserData) -> str:
    c = cur.get(user.currency)
    example = cur.format_money(123456.78, c.code)
    return f"""You are Ledgerly, a careful personal-finance assistant. Today is {user.as_of.isoformat()}; \
the user's currency is {c.code} ({c.name}), written with the symbol {c.symbol.strip()}.

RULES (strict):
1. Never do arithmetic in your head. Every figure you state must come verbatim from a tool result.
   If you need a derived figure (difference, total, percentage, projection), call the `calculator` tool first.
2. Call tools before answering any question about the user's money. Prefer the most specific tool —
   for "can I afford / should I buy" questions with a price, use `can_i_afford`.
3. Be concise: lead with the direct answer, then 2-5 short bullets of supporting detail or actions.
   Format money exactly like {example} — the {c.symbol.strip()} symbol and \
{"Indian digit grouping (thousands, then pairs)" if c.grouping == "indian" else "three-digit grouping"}.
   Tool results give plain numbers with no symbol or grouping; add them when you write the figure, and never
   convert between currencies. Use the exact category names: {", ".join(CATEGORIES)}.
4. If data is missing or the question is outside personal finance, say so. You give educational guidance,
   not regulated investment, tax or legal advice.
5. Negative transaction amounts are money spent; positive are money received."""


def build_graph(user_id: int, llm=None):
    tools = build_tools(user_id)
    if llm is None:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=settings.openai_model, temperature=0, api_key=settings.openai_api_key, timeout=60)
    model = llm.bind_tools(tools)
    user = UserData(user_id)
    sys_msg = SystemMessage(system_prompt(user))

    def agent_node(state: AgentState) -> dict:
        tool_rounds = sum(isinstance(m, ToolMessage) for m in state["messages"])
        runnable = model if tool_rounds < MAX_TOOL_ROUNDS * 2 else llm  # force a final answer
        return {"messages": [runnable.invoke([sys_msg, *state["messages"]])]}

    def route_after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "verify"

    def verify_node(state: AgentState) -> dict:
        msgs = state["messages"]
        answer = msgs[-1].content if isinstance(msgs[-1].content, str) else str(msgs[-1].content)
        sources = source_numbers([str(m.content) for m in msgs[:-1] if isinstance(m, (ToolMessage, HumanMessage, AIMessage))])
        bad = check_grounding(answer, sources)
        if bad and state.get("grounding_retries", 0) < 1:
            return {
                "grounding_retries": state.get("grounding_retries", 0) + 1,
                "ungrounded": bad,
                "messages": [HumanMessage(
                    "[automatic grounding check] These figures in your draft do not match any tool output: "
                    f"{', '.join(bad)}. Recompute them with the calculator tool (or remove them) and rewrite the full answer."
                )],
            }
        return {"ungrounded": bad}

    def route_after_verify(state: AgentState) -> str:
        return "agent" if isinstance(state["messages"][-1], HumanMessage) else END

    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_node("verify", verify_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "verify": "verify"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("verify", route_after_verify, {"agent": "agent", END: END})
    return g.compile()


def run_agent(user_id: int, question: str, history: list[dict] | None = None, llm=None) -> dict:
    """Answer a question. history: prior [{'role': 'user'|'assistant', 'content': str}] turns."""
    if llm is None and not settings.llm_enabled:
        return offline_answer(user_id, question)
    cur.set_currency(UserData(user_id).currency)   # tool output copy formats in the user's currency
    msgs: list[AnyMessage] = []
    for h in (history or [])[-10:]:
        msgs.append(HumanMessage(h["content"]) if h["role"] == "user" else AIMessage(h["content"]))
    msgs.append(HumanMessage(question))
    graph = build_graph(user_id, llm)
    final = graph.invoke({"messages": msgs, "grounding_retries": 0, "ungrounded": []},
                         {"recursion_limit": 4 * MAX_TOOL_ROUNDS})
    new_msgs = final["messages"][len(msgs):]
    tools_used = [tc["name"] for m in new_msgs if isinstance(m, AIMessage) for tc in (m.tool_calls or [])]
    answer = final["messages"][-1].content
    return {
        "answer": answer,
        "tools_used": list(dict.fromkeys(tools_used)),
        "grounded": not final.get("ungrounded"),
        "ungrounded_figures": final.get("ungrounded", []),
        "grounding_retried": final.get("grounding_retries", 0) > 0,
        "mode": "llm",
    }


# ----------------------------------------------------------------------------- offline fallback

def _fmt(v: float) -> str:
    return cur.format_money(v)


_AMOUNT_WORDS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|l|lakh|lakhs|lac|cr|crore)?\b", re.I)
_MULTIPLIERS = {"k": 1_000, "thousand": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
                "cr": 10_000_000, "crore": 10_000_000}


def _question_amount(question: str) -> int | None:
    """The price in 'can I afford a ₹60,000 phone / 1.5 lakh bike / $2k laptop', in minor units."""
    for m in _AMOUNT_WORDS.finditer(question.replace("₹", " ").replace("$", " ")):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        value *= _MULTIPLIERS.get((m.group(2) or "").lower(), 1)
        if value >= 1 and not (1900 <= value <= 2100 and not m.group(2)):
            return int(round(value * 100))
    return None


def offline_answer(user_id: int, question: str) -> dict:
    """Keyword router over the same deterministic tools, used when no OpenAI key is configured."""
    u = UserData(user_id)
    cur.set_currency(u.currency)
    q = question.lower()
    lines: list[str]
    used: str
    # first: "can I afford X next month" must not fall into the forecast branch on "next month"
    if "afford" in q and (amount := _question_amount(question)):
        used = "can_i_afford"
        from .afford import check
        monthly = any(k in q for k in ("a month", "per month", "/month", "monthly", "emi", "subscription"))
        r = check(u.tx, u.accounts, u.goals, amount, None, monthly, u.as_of)
        verdict = {"comfortable": "Yes, comfortably", "tight": "Yes, but it's tight", "not_now": "Not right now"}[r["verdict"]]
        lines = [f"**{verdict}.** " + " ".join(r["reasons"]),
                 f"- Lowest balance in the next 90 days: {_fmt(r['lowest_balance_before'])} → {_fmt(r['lowest_balance_after'])}",
                 f"- Safe to spend this month: {_fmt(r['safe_to_spend_before'])} → {_fmt(r['safe_to_spend_after'])}"]
        lines += [f"- {g['goal']}: {g['delay_months']} month(s) later" for g in r["goals"] if g["delay_months"]]
        if r["wait_until"]:
            lines.append(f"- It fits comfortably from {r['wait_until']}.")
    elif "net worth" in q or "networth" in q or "assets" in q:
        used = "get_net_worth"
        n = pl.net_worth(u.tx, u.accounts, u.assets, u.debts, u.as_of)
        lines = [f"Your net worth is **{_fmt(n['net_worth'])}**: {_fmt(n['total_assets'])} in assets minus {_fmt(n['total_liabilities'])} in liabilities."]
        lines += [f"- {a['kind'].title()}: {_fmt(a['value'])} ({a['share_pct']}%)" for a in n["asset_mix"]]
        lines.append(f"- Debts {_fmt(n['debts'])}; credit card balances {_fmt(n['card_balances'])}")
    elif any(k in q for k in ("debt", "loan", "payoff", "pay off", "avalanche", "snowball")):
        used = "get_debt_payoff_plan"
        if not plans.allows(u.plan, "debt_planner"):
            lines = [str(plans.PlanRequired("debt_planner", u.plan))]
        else:
            p = pl.debt_payoff_plan(u.debts, 0, u.as_of, u.tx)
            lines = [f"You owe {_fmt(p['total_debt'])} across {len(p['debts'])} debts (weighted APR {p['weighted_apr_pct']}%)."]
            for s in ("avalanche", "snowball", "minimum"):
                r = p["strategies"].get(s)
                if r and r["feasible"]:
                    lines.append(f"- **{s.title()}**: debt-free {r['debt_free_date']}, {_fmt(r['total_interest'])} total interest")
    elif "calendar" in q or "when are" in q or "bills due" in q:
        used = "get_bill_calendar"
        c = pl.bill_calendar(u.tx, None, u.as_of)
        upcoming = [(d["date"], e) for d in c["days"] for e in d["events"] if e["status"] == "upcoming"]
        lines = [f"{_fmt(c['still_due'])} of recurring charges are still due in {c['month']}:"]
        lines += [f"- {d}: {e['name']} {_fmt(e['amount'])}" for d, e in upcoming]
    elif "challenge" in q:
        used = "get_challenges"
        if not plans.allows(u.plan, "challenges"):
            lines = [str(plans.PlanRequired("challenges", u.plan))]
        else:
            ch = pl.challenges_overview(u.challenges, u.tx, u.as_of)
            lines = [f"{ch['active']} active challenge(s), {ch['won']} won; estimated savings {_fmt(ch['estimated_savings_total'])}."]
            lines += [f"- **{i['title']}** ({i['status']}): {i['detail']}" for i in ch["items"][:5]]
            lines += [f"- Try: {s['title']} (could save about {_fmt(s['projected_savings'])})" for s in ch["suggestions"]]
    elif "wrapped" in q or "year in review" in q or "my year" in q:
        used = "get_year_in_review"
        if not plans.allows(u.plan, "wrapped"):
            lines = [str(plans.PlanRequired("wrapped", u.plan))]
        else:
            w = pl.year_in_review(u.tx, u.as_of)
            lines = [f"**{w['label']}: you're {w['persona']}.** {w['persona_tagline']}",
                     f"- Earned {_fmt(w['income'])}, spent {_fmt(w['spending'])}, saved {_fmt(w['saved'])}",
                     f"- {w['no_spend_days']} no-spend days (longest streak {w['longest_no_spend_streak']})"]
            if w["most_visited"]:
                lines.append(f"- Most visited: {w['most_visited']['name']} ({w['most_visited']['visits']} visits)")
    elif any(k in q for k in ("unusual sub", "price", "duplicate", "overlap", "renew")):
        used = "get_unusual_subscriptions"
        flags = an.unusual_subscriptions(u.tx, u.as_of)
        lines = [f"I found {len(flags)} subscription issues:"] + [f"- **{f['title']}** — {f['detail']}" for f in flags[:8]]
    elif "subscri" in q or "recurring" in q:
        used = "get_subscriptions"
        s = an.subscriptions_summary(u.tx, u.sub_statuses, u.as_of, include_inactive=False)
        lines = [f"You have {s['active_subscriptions']} active subscriptions costing {_fmt(s['subscriptions_monthly_total'])}/month "
                 f"({_fmt(s['subscriptions_annual_total'])}/year)."]
        lines += [f"- {i['merchant']}: {_fmt(i['monthly_cost'])}/mo ({i['cadence']})" for i in s["items"] if i["kind"] == "subscription"]
    elif "anomal" in q or "suspicious" in q or "fraud" in q or "weird" in q:
        used = "get_anomalies"
        a = an.detect_anomalies(u.tx, u.as_of, 90)
        lines = [f"{len(a)} unusual transactions in the last 90 days:"] + [f"- {x['date']} {x['merchant']} {_fmt(x['amount'])}: {'; '.join(x['reasons'])}" for x in a[:8]]
    elif "suggest" in q or ("budget" in q and ("set" in q or "recommend" in q or "should" in q)):
        used = "suggest_budgets"
        b = an.suggest_budgets(u.tx, u.budgets, u.as_of)
        lines = [f"Suggested monthly budgets (based on {b['based_on_months'][0]} to {b['based_on_months'][-1]}):"]
        lines += [f"- {s['category']}: {_fmt(s['suggested'])} — {s['rationale']}" for s in b["suggestions"]]
        lines.append(f"This leaves about {_fmt(b['projected_monthly_savings'])}/month for savings.")
    elif "budget" in q:
        used = "get_budget_status"
        b = an.budget_status(u.tx, u.budgets, as_of=u.as_of)
        lines = [f"Budget status for {b['month']} (day {b['days_elapsed']} of {b['days_in_month']}):"]
        lines += [f"- {i['category']}: {_fmt(i['spent'])} of {_fmt(i['limit'])} — {i['status'].replace('_', ' ')}" for i in b["items"]]
    elif any(k in q for k in ("forecast", "cash flow", "cashflow", "next month", "run low", "run out", "cash")):
        used = "get_cashflow_forecast"
        f = an.cashflow_forecast(u.tx, u.accounts, 30, u.as_of)
        lines = [f"Over the next 30 days your cash should go from {_fmt(f['starting_balance'])} to {_fmt(f['ending_balance'])}.",
                 f"- Lowest point: {_fmt(f['lowest_balance'])} on {f['lowest_balance_date']}",
                 f"- Scheduled income: {_fmt(f['scheduled_income'])}; recurring charges: {_fmt(f['scheduled_recurring_charges'])}",
                 f"- Estimated variable spending: {_fmt(f['estimated_variable_spending'])}"]
    elif "safe" in q or "afford" in q or "left to spend" in q:
        used = "get_safe_to_spend"
        s = an.safe_to_spend(u.tx, as_of=u.as_of)
        lines = [f"Safe to spend for the rest of {s['month']}: **{_fmt(s['safe_to_spend'])}** (about {_fmt(s['per_day'])}/day).",
                 f"- Recurring charges still due: {_fmt(s['recurring_still_due'])}",
                 f"- Savings target held back: {_fmt(s['savings_target'])}"]
    elif "health" in q or "score" in q or "doing" in q:
        used = "get_health_score"
        h = an.health_score(u.tx, u.accounts, u.budgets, u.as_of)
        lines = [f"Your financial health score is **{h['score']}/100 ({h['grade']})**."] + [f"- {c['name']}: {c['score']}/100 — {c['detail']}" for c in h["components"]]
    elif "goal" in q:
        used = "get_goals"
        lines = ["Your goals:"] + [f"- {g['name']}: {_fmt(g['saved'])} of {_fmt(g['target'])}; needs {_fmt(g['required_monthly'])}/month — {'on track' if g['on_track'] else 'behind'}"
                                  for g in an.goals_progress(u.tx, u.goals, u.as_of)]
    else:
        used = "get_category_breakdown"
        month = next(iter(re.findall(r"\d{4}-\d{2}", q)), None)
        b = an.category_breakdown(u.tx, month, u.as_of)
        lines = [f"You spent {_fmt(b['total'])} in {b['month']}{' so far' if b['partial'] else ''}:"]
        lines += [f"- {c['category']}: {_fmt(c['amount'])} ({c['share_pct']}%)" for c in b["categories"][:8]]
    lines.append("\n_Offline mode: add OPENAI_API_KEY to backend/.env for free-form questions._")
    return {"answer": "\n".join(lines), "tools_used": [used], "grounded": True, "ungrounded_figures": [],
            "grounding_retried": False, "mode": "offline"}
