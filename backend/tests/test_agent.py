"""Agent graph tests with a scripted chat model (no network)."""
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent import check_grounding, run_agent, source_numbers


class ScriptedLLM(BaseChatModel):
    script: list[Any]
    seen: list[list] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        step = self.script.pop(0)
        msg = step(messages) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_grounding_checker():
    sources = source_numbers(['{"monthly_cost":17.99,"annual_cost":215.88}', "result 16.1394"])
    assert check_grounding("Netflix costs $17.99/mo ($215.88/yr), up 16.1%.", sources) == []
    assert check_grounding("That's about $220 a year.", sources) == ["$220"]
    # dates, years and small counts are not treated as money claims
    assert check_grounding("On 2026-09-15 you had 3 charges in 2026.", sources) == []


def test_agent_uses_tools_and_passes_grounding(user):
    def answer_from_tool(messages):
        tool_msg = next(m for m in reversed(messages) if isinstance(m, ToolMessage))
        import json
        data = json.loads(tool_msg.content)
        return AIMessage(f"You pay ${data['subscriptions_monthly_total']:,.2f} per month for subscriptions.")

    llm = ScriptedLLM(script=[
        AIMessage("", tool_calls=[{"name": "get_subscriptions", "args": {}, "id": "call_1"}]),
        answer_from_tool,
    ])
    out = run_agent(1, "How much do I spend on subscriptions?", llm=llm)
    assert out["tools_used"] == ["get_subscriptions"]
    assert out["grounded"] and not out["grounding_retried"]
    assert out["answer"].startswith("You pay $")


def test_agent_hallucinated_number_triggers_retry(user):
    llm = ScriptedLLM(script=[
        AIMessage("", tool_calls=[{"name": "calculator", "args": {"expression": "17.99 * 12"}, "id": "c1"}]),
        AIMessage("Netflix will cost you $300.00 a year."),               # wrong -> guard rejects
        lambda msgs: AIMessage("Netflix will cost you $215.88 a year."),  # corrected
    ])
    out = run_agent(1, "What does Netflix cost per year?", llm=llm)
    assert out["grounding_retried"] and out["grounded"]
    assert "$215.88" in out["answer"]
    retry_prompt = [m for m in llm.seen[-1] if isinstance(m, HumanMessage)][-1].content
    assert "$300.00" in retry_prompt


def test_offline_mode_answers(user):
    from app.agent import offline_answer
    out = offline_answer(1, "Any unusual subscriptions?")
    assert out["mode"] == "offline" and "Spotify" in out["answer"]
    assert offline_answer(1, "What's my net worth?")["tools_used"] == ["get_net_worth"]
    assert "Avalanche" in offline_answer(1, "How fast can I pay off my debt?")["answer"]
    assert offline_answer(1, "Show my money wrapped")["tools_used"] == ["get_year_in_review"]


def test_new_tools_are_grounded(user):
    def answer(messages):
        import json
        data = json.loads(next(m for m in reversed(messages) if isinstance(m, ToolMessage)).content)
        a = data["strategies"]["avalanche"]
        return AIMessage(f"With avalanche you're debt-free by {a['debt_free_date']} and pay ${a['total_interest']:,.2f} in interest.")

    llm = ScriptedLLM(script=[
        AIMessage("", tool_calls=[{"name": "get_debt_payoff_plan", "args": {"extra_monthly": 200}, "id": "d1"}]),
        answer,
    ])
    out = run_agent(1, "When will I be debt free if I pay $200 extra?", llm=llm)
    assert out["tools_used"] == ["get_debt_payoff_plan"] and out["grounded"]
