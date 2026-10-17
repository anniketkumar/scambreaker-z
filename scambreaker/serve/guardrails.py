"""Output checks for generated text, plus deterministic fallback templates.

The LLM explains a decision that the predictive models already made; it never decides. If a
generation breaks a rule (links, phone numbers, wrong amounts, too long) or times out, the
customer sees a vetted template instead.
"""
from __future__ import annotations

import re

URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|net|org|sg|io|ly|me|xyz)\b)", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s-]{6,}\d")
NUM_RE = re.compile(r"(?:S?\$|SGD|USD)\s?(\d[\d,]*(?:\.\d+)?)", re.I)  # money amounts only
SECRET_RE = re.compile(r"\b(otp|one[- ]time|password|pin)\b", re.I)


def check(text: str, amounts: list[float], max_chars: int = 600) -> tuple[bool, str]:
    if not text or len(text.strip()) < 12:
        return False, "empty"
    if len(text) > max_chars:
        return False, "too long"
    if URL_RE.search(text):
        return False, "contains a link"
    if PHONE_RE.search(text):
        return False, "contains a phone number"
    if SECRET_RE.search(text) and not re.search(r"(never|don't|do not|绝不|不要)", text, re.I):
        return False, "mentions credentials"
    for raw in NUM_RE.findall(text):
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if v >= 100 and not any(abs(v - a) <= max(1.0, 0.01 * a) for a in amounts):
            return False, f"unexpected amount {raw}"
    return True, "ok"


HINTS = {
    "gov_impersonation": ("Government officers will never ask you to move money to a 'safe account'.",
                          "政府官员绝不会要求您把钱转到“安全账户”。"),
    "investment": ("Investments that promise guaranteed high returns are almost always scams.",
                   "保证高回报的投资几乎都是骗局。"),
    "job_task": ("Real jobs never ask you to pay first to unlock commission.",
                 "真正的工作不会要求您先付钱才能领取佣金。"),
    "romance": ("Someone you have only met online asking for money is a common scam.",
                "只在网上认识的人向您要钱，是常见的诈骗手法。"),
    "ecommerce": ("Sellers who ask for a bank transfer outside the platform are high-risk.",
                  "卖家要求您在平台外转账付款，风险很高。"),
}


def customer_template(typ: str, amount_sgd: float, decision: str, lang: str) -> str:
    en, zh = HINTS.get(typ, HINTS["gov_impersonation"])
    if lang == "zh":
        verb = "暂停" if decision != "BLOCK" else "拦截"
        return (f"{{NAME}}，为了保护您，我们{verb}了这笔 S${amount_sgd:,.0f} 的转账。{zh}"
                "请不要在通话中转账。我们会致电您登记的电话号码。")
    verb = "paused" if decision != "BLOCK" else "stopped"
    return (f"{{NAME}}, we have {verb} this S${amount_sgd:,.0f} transfer to protect you. {en} "
            "Please do not send money while anyone is on the phone with you - we will call you on your "
            "registered number.")


def contact_template(typ_label: str, amount_sgd: float) -> str:
    return (f"Demo Bank: a S${amount_sgd:,.0f} transfer by {{NAME}} was paused as a likely "
            f"{typ_label.lower()}. Please call {{NAME}} now and check they are safe.")
