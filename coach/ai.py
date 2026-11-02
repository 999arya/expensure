"""Optional, bounded AI selection of verified explanations. Never model arithmetic."""
import json
import os
import urllib.request

from django.conf import settings


def query_ai_coach(prompt_context, user_question, affordability_eval=None, *, allow_ai=False):
    evaluation = affordability_eval
    if evaluation is None:
        return False, {
            "title": "Recorded financial summary",
            "verdict": "Review your plan",
            "body": "Check your confirmed balances and upcoming bills.",
            "reason": "Based on recorded data only.",
            "assumptions": "Capture may be incomplete.",
            "source": "deterministic",
        }
    result = {
        "title": "Purchase affordability",
        "verdict": evaluation.verdict,
        "body": evaluation.reason,
        "reason": evaluation.reason,
        "amount_paise": evaluation.requested_amount_paise,
        "assumptions": evaluation.assumptions,
        "source": "deterministic",
    }
    providers = []
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        providers.append(("https://api.groq.com/openai/v1/chat/completions", groq_key, settings.GROQ_MODEL))
    if not allow_ai or not providers:
        return False, result

    options = {
        "calculation": evaluation.reason,
        "assumptions": evaluation.assumptions,
        "account_limits": "Combined allowance does not guarantee an individual account stays above its floor. Check the paying account before spending.",
        "coverage": evaluation.coverage_warning or "Balances are estimates; unrecorded movements can change this assessment.",
    }
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": [evaluation.verdict]},
            "explanations": {"type": "array", "items": {"type": "string", "enum": list(options)}},
        },
        "required": ["verdict", "explanations"],
        "additionalProperties": False,
    }
    payload_base = {
        "messages": [
            {"role": "system", "content": "Select relevant verified explanations for the purchase question. Include calculation and coverage. Use only supplied IDs; do not calculate or suggest actions."},
            {"role": "user", "content": json.dumps({"summary": prompt_context, "question": user_question, "verdict": evaluation.verdict, "supported_explanations": options})},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "verified_coach", "strict": True, "schema": schema}},
        "max_completion_tokens": 300,
        "store": False,
    }
    for endpoint, key, model in providers:
        try:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({**payload_base, "model": model}).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Expensure/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                output = json.loads(response.read(32768))
            choice = output["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                continue
            parsed = json.loads(choice["message"]["content"])
            if not isinstance(parsed, dict) or set(parsed) != {"verdict", "explanations"} or parsed["verdict"] != evaluation.verdict:
                continue
            selected = parsed["explanations"]
            if not isinstance(selected, list) or not 2 <= len(selected) <= 4 or any(not isinstance(item, str) or item not in options for item in selected):
                continue
            if len(set(selected)) != len(selected) or not {"calculation", "coverage"}.issubset(selected):
                continue
            result.update(body=" ".join(options[item] for item in selected), source="ai")
            return True, result
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
    return False, result


def prioritize_goal_steps(steps, *, allow_ai=False):
    """AI may reorder verified steps, but cannot invent amounts or actions."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not allow_ai or not key:
        return False, steps
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Prioritize these verified financial goal steps. Return JSON with an order array containing every supplied integer ID exactly once. Do not add advice."},
            {"role": "user", "content": json.dumps(dict(enumerate(steps)))},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 150,
        "store": False,
    }
    try:
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Expensure/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            output = json.loads(response.read(32768))
        choice = output["choices"][0]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            return False, steps
        parsed = json.loads(choice["message"]["content"])
        order = parsed.get("order") if isinstance(parsed, dict) else None
        if (isinstance(order, list) and all(type(i) is int for i in order)
                and sorted(order) == list(range(len(steps)))):
            return True, [steps[i] for i in order]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return False, steps


def choose_history_target(summary, *, allow_ai=False):
    """Select a server-calculated candidate; never accept a model-created amount."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not allow_ai or not key:
        return False, "cautious"
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Choose a monthly savings target from the calculated options. Amounts are integer paise. Prefer cautious for declining or variable surplus. Choose balanced only for stable history. Return only JSON: {\"choice\": \"cautious\"} or {\"choice\": \"balanced\"}. Do not calculate new amounts."},
            {"role": "user", "content": json.dumps(summary)},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 100,
        "store": False,
    }
    try:
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Expensure/1.0"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            output = json.loads(response.read(32768))
        item = output["choices"][0]
        if item.get("finish_reason") != "stop" or item["message"].get("refusal"):
            return False, "cautious"
        parsed = json.loads(item["message"]["content"])
        if isinstance(parsed, dict) and set(parsed) == {"choice"} and isinstance(parsed["choice"], str) and parsed["choice"] in {"cautious", "balanced"}:
            return True, parsed["choice"]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return False, "cautious"
