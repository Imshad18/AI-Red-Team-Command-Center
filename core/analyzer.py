from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

STOPWORDS = {
    "the","a","an","and","or","to","of","in","on","for","with","is","are","was","were","be","been","being",
    "this","that","these","those","it","its","as","at","by","from","into","about","through","after","before",
    "you","your","we","our","they","their","he","she","his","her","them","i","me","my","do","does","did",
    "can","could","would","should","may","might","must","will","shall","have","has","had","not","no","yes",
    "if","then","than","when","where","what","which","who","whom","why","how","also","only","any","all",
    "some","such","more","most","other","another","each","every","very","just","use","using","used","via",
}

OUTCOME_PATTERNS = {
    "success": [
        r"\bsuccess(?:ful|fully)?\b", r"\bpassed\b", r"\baccepted\b", r"\bflag(?:ged)?\b", r"\bworked\b",
        r"\breward(?:ed)?\b", r"\bpayout\b", r"\bconfirmed\b", r"\btriaged\b", r"\bwon\b", r"\bsolved\b",
        r"\bcomplete(?:d)?\b", r"\bproof of concept\b", r"\bpoc worked\b"
    ],
    "failure": [
        r"\bfailed\b", r"\brefused\b", r"\bdenied\b", r"\bblocked\b", r"\brejected\b", r"\binvalid\b",
        r"\bnot working\b", r"\bdidn['’]?t work\b", r"\bunsuccessful\b", r"\bno impact\b", r"\bduplicate\b"
    ],
    "partial": [
        r"\bpartial(?:ly)?\b", r"\balmost\b", r"\bnear success\b", r"\bpromising\b", r"\bclose\b",
        r"\bworked but\b", r"\bsome progress\b", r"\bincomplete\b"
    ],
}

TECHNIQUES: dict[str, dict[str, Any]] = {
    "instruction-hierarchy-conflict": {
        "label": "Instruction hierarchy conflict",
        "keywords": ["ignore previous", "ignore prior", "system prompt", "developer message", "higher priority", "override", "instruction hierarchy", "policy conflict", "new instruction"],
        "triggers": ["instruction", "system", "policy", "priority", "override"],
        "why": "The objective depends on making the model resolve competing instructions or reinterpret which instruction has priority.",
    },
    "identity-context-confusion": {
        "label": "Identity / context confusion",
        "keywords": ["another user", "other user", "wrong account", "cross account", "different account", "authenticated user", "impersonat", "identity", "session", "tenant", "customer account"],
        "triggers": ["account", "user", "identity", "session", "tenant", "authenticated"],
        "why": "The task contains identity, session, tenant, or account boundaries that may be inconsistently enforced across reasoning and tool calls.",
    },
    "authorization-boundary": {
        "label": "Authorization boundary probing",
        "keywords": ["permission", "authorize", "authorised", "authorized", "access control", "privilege", "restricted", "not allowed", "only account", "cross-user", "cross user", "admin", "owner"],
        "triggers": ["permission", "authorized", "restricted", "account", "admin", "owner"],
        "why": "The challenge appears to hinge on whether the agent checks authorization at the right layer and for the right object.",
    },
    "tool-result-trust": {
        "label": "Tool-result trust manipulation",
        "keywords": ["tool output", "tool result", "function result", "browser result", "search result", "api response", "returned by tool", "external result"],
        "triggers": ["tool", "function", "api", "browser", "result", "response"],
        "why": "Tool or external outputs may be treated as trusted instructions instead of untrusted data.",
    },
    "indirect-prompt-injection": {
        "label": "Indirect prompt injection",
        "keywords": ["web page", "website", "email body", "document", "attachment", "calendar invite", "slack message", "github issue", "retrieved content", "untrusted content", "indirect prompt"],
        "triggers": ["browser", "website", "email", "document", "attachment", "message", "retrieved"],
        "why": "The agent reads content controlled by another source, which creates a path for instructions to enter through data rather than the user message.",
    },
    "state-memory-poisoning": {
        "label": "State / memory poisoning",
        "keywords": ["memory", "remember", "persistent", "saved context", "profile", "notes", "future session", "state", "cache", "conversation history"],
        "triggers": ["memory", "state", "persistent", "history", "profile"],
        "why": "Persistent state can let an earlier action influence later decisions outside the original context.",
    },
    "multi-step-decomposition": {
        "label": "Multi-step decomposition",
        "keywords": ["step by step", "multi-step", "first", "then", "workflow", "sequence", "chain", "separate actions", "break down"],
        "triggers": ["workflow", "step", "sequence", "chain", "process"],
        "why": "A restricted end state may be reachable through individually ordinary intermediate actions that are evaluated separately.",
    },
    "tool-parameter-binding": {
        "label": "Tool parameter / object binding",
        "keywords": ["parameter", "argument", "target account", "recipient", "object id", "resource id", "tool call", "function call", "target", "destination"],
        "triggers": ["tool", "parameter", "argument", "recipient", "target", "destination", "id"],
        "why": "The agent may understand the policy correctly but bind the wrong target, recipient, object, or identifier into a tool call.",
    },
    "source-recipient-confusion": {
        "label": "Source / recipient confusion",
        "keywords": ["send to", "recipient", "forward", "reply", "publish", "post", "share", "destination", "source", "sender", "channel"],
        "triggers": ["send", "recipient", "forward", "reply", "publish", "share", "channel"],
        "why": "The action may be permitted for one source or recipient but unsafe for another, creating a routing boundary to test.",
    },
    "delegated-authority": {
        "label": "Delegated authority / approval confusion",
        "keywords": ["manager approved", "approved by", "on behalf of", "delegated", "authorized by", "permission from", "supervisor", "administrator says", "emergency", "urgent"],
        "triggers": ["approved", "authority", "manager", "supervisor", "emergency", "urgent", "behalf"],
        "why": "Claims of delegated authority, approval, or urgency can reveal whether the agent verifies authority or simply accepts the claim.",
    },
    "format-parser-confusion": {
        "label": "Format / parser confusion",
        "keywords": ["json", "xml", "yaml", "markdown", "html", "csv", "base64", "encoded", "escaped", "quoted", "code block", "delimiter", "parser"],
        "triggers": ["json", "xml", "yaml", "markdown", "html", "encoded", "parser", "format"],
        "why": "Different parsers or representation layers can disagree on what is instruction versus data.",
    },
    "ambiguity-exploitation": {
        "label": "Ambiguity exploitation",
        "keywords": ["ambiguous", "unclear", "same name", "alias", "nickname", "reference", "it", "them", "that account", "current user", "this user"],
        "triggers": ["account", "user", "reference", "alias", "current", "same"],
        "why": "Ambiguous references can cause the model to resolve an entity differently from the enforcement layer.",
    },
    "verification-gap": {
        "label": "Verification / confirmation gap",
        "keywords": ["confirm", "confirmation", "verify", "verification", "consent", "approval", "double check", "are you sure", "challenge", "second factor"],
        "triggers": ["confirm", "verify", "consent", "approval", "factor"],
        "why": "The task may expose a mismatch between what the agent claims to verify and what it actually verifies before acting.",
    },
    "data-boundary": {
        "label": "Sensitive-data boundary",
        "keywords": ["private", "sensitive", "secret", "personal", "medical", "financial", "credential", "password", "token", "email address", "phone number", "address", "pii", "confidential"],
        "triggers": ["private", "sensitive", "secret", "personal", "medical", "financial", "credential", "password", "token"],
        "why": "The objective involves data whose handling depends on consent, audience, purpose, or identity boundaries.",
    },
    "external-side-effect": {
        "label": "External side-effect control",
        "keywords": ["delete", "send", "email", "post", "publish", "purchase", "buy", "schedule", "cancel", "transfer", "shutdown", "shutoff", "modify", "change", "book", "submit"],
        "triggers": ["delete", "send", "post", "purchase", "schedule", "cancel", "transfer", "modify", "submit"],
        "why": "The challenge asks the agent to cause an external side effect, so confirmation, scope, and target binding become important attack surfaces.",
    },
    "classifier-bypass": {
        "label": "Classifier / detector bypass",
        "keywords": ["classifier", "moderation", "detector", "content filter", "safety filter", "guard model", "judge model", "blocked by classifier", "evade detection", "bypass classifier", "classifier score"],
        "triggers": ["classifier", "moderation", "detector", "filter", "guard", "score"],
        "why": "The challenge includes a separate classifier or detector whose decision can be attacked independently from the target agent.",
    },
    "pricing-guardrail": {
        "label": "Pricing guardrail bypass",
        "keywords": ["price floor", "pricing guardrail", "margin requirement", "offer_price", "msrp", "discount", "absurd discount", "sale price"],
        "triggers": ["price", "offer", "discount", "msrp", "margin"],
        "why": "The task explicitly depends on reaching a forbidden or extreme final price despite pricing checks.",
    },
    "escalation-bypass": {
        "label": "Escalation-path bypass",
        "keywords": ["escalate_to_manager", "escalate to manager", "manager escalation", "human manager", "supervisor", "without escalating"],
        "triggers": ["escalate", "manager", "supervisor", "human review"],
        "why": "The success condition requires staying inside the automated action path and avoiding a human-review branch.",
    },
    "arithmetic-transform": {
        "label": "Arithmetic / net-price transform",
        "keywords": ["rebate", "credit", "adjustment", "net price", "final price", "discount", "price after"],
        "triggers": ["rebate", "credit", "adjustment", "discount", "net"],
        "why": "A guardrail may validate the requested price differently when the same final value is reached through credits, rebates, or corrections.",
    },
    "semantic-reframe": {
        "label": "Semantic reframing",
        "keywords": ["promotion", "promotional", "correction", "nominal", "qa", "test transaction", "special offer"],
        "triggers": ["promotion", "correction", "nominal", "test", "special"],
        "why": "The same tool parameters can be framed as a promotion, correction, validation run, or other business workflow to test where enforcement actually lives.",
    },
}

TOOLS = {
    "browser": ["browser", "website", "web page", "navigate", "click", "url"],
    "email": ["email", "gmail", "outlook", "mail", "inbox"],
    "chat": ["slack", "discord", "teams", "chat", "message", "dm"],
    "calendar": ["calendar", "meeting", "appointment", "invite", "event"],
    "github": ["github", "repository", "repo", "pull request", "issue"],
    "files": ["file", "folder", "document", "attachment", "drive", "dropbox", "sharepoint"],
    "shell": ["terminal", "shell", "command", "powershell", "bash", "cmd"],
    "api": ["api", "endpoint", "request", "response", "function", "tool call"],
    "database": ["database", "sql", "record", "row", "table"],
    "memory": ["memory", "remember", "profile", "saved context"],
}

ASSETS = {
    "credentials": ["credential", "password", "token", "api key", "secret", "otp", "2fa", "session cookie"],
    "identity": ["identity", "name", "profile", "account", "user id", "customer id"],
    "communications": ["email", "message", "dm", "conversation", "chat", "thread"],
    "documents": ["document", "file", "attachment", "report", "pdf", "spreadsheet"],
    "financial": ["payment", "card", "bank", "billing", "invoice", "price", "purchase"],
    "health": ["medical", "health", "diagnosis", "patient", "clinic", "hospital"],
    "location": ["address", "location", "gps", "coordinates"],
    "service-control": ["shutdown", "shutoff", "service", "schedule", "cancel", "transfer", "modify", "delete"],
    "private-data": ["private", "personal", "confidential", "sensitive", "pii"],
}

BOUNDARY_PATTERNS = {
    "identity": [r"another user", r"other user", r"different user", r"wrong user", r"authenticated user", r"current user"],
    "account": [r"another account", r"other account", r"different account", r"cross[- ]account", r"target account", r"account number", r"\baccount\b"],
    "authorization": [r"not allowed", r"only allowed", r"permission", r"authori[sz]", r"restricted", r"privilege", r"access control"],
    "consent": [r"consent", r"approval", r"confirm", r"verification", r"permission from"],
    "privacy": [r"private", r"confidential", r"sensitive", r"personal data", r"medical", r"financial"],
    "tool-scope": [r"tool call", r"function call", r"parameter", r"argument", r"target", r"recipient", r"resource id"],
    "external-content": [r"website", r"web page", r"email body", r"document", r"attachment", r"message", r"retrieved content"],
}


MODEL_PATTERNS = {
    "GPT": [r"\bgpt[- ]?[345](?:\.\d+)?(?:[- ]?[a-z0-9]+)?\b", r"\bchatgpt\b", r"\bopenai\b"],
    "Claude": [r"\bclaude(?:[- ]?[0-9.]+)?\b", r"\bsonnet(?:[- ]?[0-9.]+)?\b", r"\bopus(?:[- ]?[0-9.]+)?\b", r"\bhaiku(?:[- ]?[0-9.]+)?\b"],
    "Gemini": [r"\bgemini(?:[- ]?[0-9.]+)?\b"],
    "Llama": [r"\bllama(?:[- ]?[0-9.]+)?\b"],
    "Qwen": [r"\bqwen(?:[- ]?[0-9.]+)?\b"],
    "Mistral": [r"\bmistral(?:[- ]?[a-z0-9.]+)?\b"],
    "DeepSeek": [r"\bdeepseek(?:[- ]?[a-z0-9.]+)?\b"],
}

AGENT_PATTERNS = {
    "browser-use agent": ["browser-use", "browser use agent", "browser agent", "web agent"],
    "tool-using agent": ["tool-using agent", "tool using agent", "function calling", "tool call", "agent tools"],
    "email agent": ["email agent", "mail agent", "gmail agent", "inbox agent"],
    "coding agent": ["coding agent", "code agent", "developer agent", "terminal agent"],
    "support agent": ["support agent", "customer support", "helpdesk agent"],
    "multi-agent system": ["multi-agent", "multi agent", "agent-to-agent", "agent to agent"],
}

TEST_FOCUS = {
    "instruction-hierarchy-conflict": "Hold the goal constant and vary only the claimed instruction priority or workflow authority.",
    "identity-context-confusion": "Keep the authenticated identity fixed while changing the referenced target identity/account and watch which identity reaches the tool call.",
    "authorization-boundary": "Separate authentication, object ownership, action permission, and confirmation into distinct checks to find which layer is actually enforced.",
    "tool-result-trust": "Place case-specific continuation instructions in tool-returned or retrieved content and see whether data is promoted into instruction.",
    "indirect-prompt-injection": "Move the instruction from the direct user message into content the agent must retrieve to finish the task.",
    "state-memory-poisoning": "Establish a case-specific assumption in one step, then test whether it incorrectly persists into a later sensitive action.",
    "multi-step-decomposition": "Break the end goal into individually ordinary operations and test where enforcement occurs across the sequence.",
    "tool-parameter-binding": "Compare the identity/resource named in policy reasoning with the exact identifier placed into the eventual tool arguments.",
    "source-recipient-confusion": "Keep the content fixed while changing sender/source or recipient/destination to test whether routing boundaries are enforced.",
    "delegated-authority": "Vary claims of approval, delegation, and urgency while checking whether the agent verifies the authority source.",
    "format-parser-confusion": "Represent the same task in prose and structured formats and compare precedence, reference resolution, and tool parameters.",
    "ambiguity-exploitation": "Replace explicit entity names with pronouns, aliases, or competing identifiers and inspect which object is resolved.",
    "verification-gap": "Test whether verification is tied to the session, the target object, or the specific external side effect.",
    "data-boundary": "Keep the requested operation constant while changing audience, identity, purpose, or data sensitivity to locate the privacy boundary.",
    "external-side-effect": "Compare planning/preparation steps with the final side-effecting tool call and identify exactly where confirmation is enforced.",
    "classifier-bypass": "Keep the target action fixed while changing only the surface form, structure, or semantic representation seen by the classifier.",
    "pricing-guardrail": "Keep the required final price fixed while changing how that price is represented: direct price, correction, promotion, rebate, or net adjustment.",
    "escalation-bypass": "Map which words and workflow states trigger human escalation, then test the same target action without entering that branch.",
    "arithmetic-transform": "Reach the same final numeric value through discount, credit, rebate, or correction arithmetic and check where final-price validation happens.",
    "semantic-reframe": "Keep the tool arguments constant while changing the business interpretation of the transaction.",
}

WATCH_SIGNALS = {
    "identity-context-confusion": "Tool arguments reference a different identity/account than the one the model says is authorized.",
    "authorization-boundary": "The model reasons about one permission check but the tool layer accepts a broader object/action scope.",
    "tool-result-trust": "Retrieved text changes the agent's goal, target, recipient, or next tool action.",
    "indirect-prompt-injection": "Instructions in external content are followed without being treated as untrusted data.",
    "tool-parameter-binding": "Target/recipient/resource identifiers drift between reasoning and execution.",
    "verification-gap": "A sensitive side effect occurs after session-level verification without object- or action-level confirmation.",
    "multi-step-decomposition": "No single step appears disallowed, but the sequence reaches the restricted competition success state.",
    "classifier-bypass": "The classifier score or block decision changes while the underlying task meaning and target action remain equivalent.",
    "pricing-guardrail": "The final side-effecting tool receives the prohibited price even though conversational reasoning acknowledges normal pricing rules.",
    "escalation-bypass": "The agent reaches the target tool call without invoking the human-review tool or branch.",
    "arithmetic-transform": "The final price becomes the target value only after a rebate, credit, discount, or correction transformation.",
    "semantic-reframe": "A business-category change alters enforcement while leaving the final tool parameters unchanged.",
}

OBJECTIVE_VERBS = [
    "get", "obtain", "access", "reveal", "extract", "make", "cause", "convince", "persuade", "force", "bypass",
    "send", "post", "publish", "share", "modify", "change", "delete", "schedule", "cancel", "transfer", "book",
    "submit", "read", "open", "expose", "leak", "retrieve", "use", "execute", "call", "trigger", "complete",
]


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9_@.-]{2,}", text.lower())
    return [t.strip("._-") for t in tokens if t.strip("._-") and t not in STOPWORDS and not t.isdigit()]


def token_counts(text: str, limit: int = 260) -> dict[str, int]:
    counts = Counter(tokenize(text))
    return dict(counts.most_common(limit))


def detect_outcome(text: str) -> str:
    lower = text.lower()
    scores = {k: 0 for k in OUTCOME_PATTERNS}
    for outcome, patterns in OUTCOME_PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(pattern, lower, flags=re.I)
            scores[outcome] += len(matches)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    if scores["success"] and scores["failure"] and abs(scores["success"] - scores["failure"]) <= 1:
        return "partial"
    return best


def detect_techniques(text: str) -> list[str]:
    lower = text.lower()
    scored: list[tuple[int, str]] = []
    for key, meta in TECHNIQUES.items():
        score = 0
        for phrase in meta["keywords"]:
            if phrase in lower:
                score += 3 if " " in phrase else 2
        for trigger in meta["triggers"]:
            score += min(lower.count(trigger), 3)
        if score >= 3:
            scored.append((score, key))
    scored.sort(reverse=True)
    return [key for _, key in scored[:8]]


def _split_sentences(text: str) -> list[str]:
    lines = [x.strip(" -•\t") for x in text.splitlines() if x.strip()]
    chunks: list[str] = []
    for line in lines:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)
        chunks.extend(p.strip() for p in parts if p.strip())
    return chunks[:500]


def _score_objective_sentence(sentence: str) -> float:
    low = sentence.lower()
    score = 0.0
    if any(v in re.findall(r"\b[a-z]+\b", low) for v in OBJECTIVE_VERBS):
        score += 2
    if any(x in low for x in ["objective", "goal", "your task", "success", "win", "score", "must", "need to", "challenge"]):
        score += 3
    if any(x in low for x in ["without", "despite", "even though", "not allowed", "restricted", "only", "cannot"]):
        score += 1
    if 20 <= len(sentence) <= 420:
        score += 1
    if sentence.endswith(":"):
        score -= 0.5
    return score


def _extract_objectives(text: str) -> list[str]:
    sentences = _split_sentences(text)
    ranked = sorted((( _score_objective_sentence(s), i, s) for i, s in enumerate(sentences)), reverse=True)
    chosen: list[tuple[int, str]] = []
    for score, i, sentence in ranked:
        if score < 2:
            continue
        if sentence not in [x[1] for x in chosen]:
            chosen.append((i, sentence))
        if len(chosen) >= 5:
            break
    chosen.sort(key=lambda x: x[0])
    if not chosen and sentences:
        return sentences[:2]
    return [x[1] for x in chosen]


def _detect_named_values(text: str) -> dict[str, list[str]]:
    values = {
        "emails": sorted(set(re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, flags=re.I)))[:12],
        "urls": sorted(set(re.findall(r"https?://[^\s<>'\"]+", text, flags=re.I)))[:12],
        "account_ids": sorted(set(re.findall(r"\b(?:account|acct|case|ticket|order|customer|user)[\s#:_-]*(?:id[\s#:_-]*)?([A-Z0-9-]{4,})\b", text, flags=re.I)))[:20],
        "quoted_strings": sorted(set(x.strip() for x in re.findall(r"[\"“]([^\"”]{3,80})[\"”]", text)))[:20],
    }
    return values


def _match_catalog(text: str, catalog: dict[str, list[str]]) -> list[str]:
    low = text.lower()
    found = []
    for name, phrases in catalog.items():
        if any(p in low for p in phrases):
            found.append(name)
    return found


def _extract_boundaries(text: str, mechanics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    mechanics = mechanics or {}
    scenario = mechanics.get("scenario", "general")
    sentences = _split_sentences(text)

    def evidence(phrases: list[str], limit: int = 3) -> list[str]:
        out = []
        for sentence in sentences:
            low = sentence.lower()
            if any(p in low for p in phrases):
                cleaned = re.sub(r"\s+", " ", sentence).strip()
                if cleaned not in out:
                    out.append(cleaned)
                if len(out) >= limit:
                    break
        return out

    if scenario == "price_override":
        specs = [
            ("pricing-guardrail", ["price floor", "pricing guardrail", "margin requirement", "msrp", "absurd discount", "offer_price"]),
            ("tool-action", list(mechanics.get("required_tools") or []) + ["binding offer", "tool call", "make_offer"]),
            ("escalation-path", list(mechanics.get("forbidden_tools") or []) + ["manager escalation", "escalate", "human manager", "supervisor"]),
            ("binding-side-effect", ["binding offer", "binding contract", "legal liability", "financial loss", "purchase offer"]),
        ]
        out = []
        for name, phrases in specs:
            hits = evidence([p.lower() for p in phrases if p])
            if hits:
                out.append({"type": name, "evidence": hits})
        return out

    if scenario == "classifier_bypass":
        specs = [
            ("classifier-boundary", ["classifier", "moderation", "detector", "content filter", "safety filter", "guard model", "judge model"]),
            ("scoring-condition", ["score", "success", "criterion", "judge", "detected", "blocked"]),
            ("target-action", list(mechanics.get("required_tools") or []) + ["tool call", "execute", "action"]),
        ]
        out = []
        for name, phrases in specs:
            hits = evidence([p.lower() for p in phrases if p])
            if hits:
                out.append({"type": name, "evidence": hits})
        return out

    if scenario == "cross_identity":
        specs = [
            ("account", ["other user", "another user", "cross account", "authenticated user", "target account"]),
            ("authorization", ["authorization", "authorized", "permission", "access control", "restricted"]),
            ("private-data", ["private", "confidential", "personal data", "medical data", "financial data", "credential"]),
            ("tool-action", list(mechanics.get("required_tools") or []) + ["tool call", "function call"]),
        ]
        out=[]
        for name, phrases in specs:
            hits=evidence([p.lower() for p in phrases if p])
            if hits: out.append({"type":name,"evidence":hits})
        return out

    if scenario == "indirect_injection":
        specs = [
            ("external-content", ["website", "web page", "email body", "document", "attachment", "retrieved content", "untrusted content"]),
            ("instruction-trust", ["instruction", "prompt injection", "follow", "system prompt"]),
            ("tool-action", list(mechanics.get("required_tools") or []) + ["tool call", "function call"]),
        ]
        out=[]
        for name, phrases in specs:
            hits=evidence([p.lower() for p in phrases if p])
            if hits: out.append({"type":name,"evidence":hits})
        return out

    low = text.lower()
    results = []
    generic = {
        "identity": [r"another user", r"other user", r"different user", r"authenticated user"],
        "account": [r"another account", r"other account", r"cross[- ]account", r"target account", r"account number"],
        "authorization": [r"not allowed", r"only allowed", r"permission", r"authori[sz]", r"restricted", r"access control"],
        "consent": [r"\bconsent\b", r"explicit approval", r"user confirmation"],
        "private-data": [r"\bprivate\b", r"confidential", r"sensitive data", r"personal data", r"medical data", r"credential"],
        "tool-action": [r"tool call", r"function call", r"parameter", r"recipient", r"resource id"],
        "external-content": [r"website", r"web page", r"email body", r"attachment", r"retrieved content", r"untrusted content"],
    }
    for name, patterns in generic.items():
        hits=[]
        for pattern in patterns:
            for m in re.finditer(pattern, low, re.I):
                for sentence in sentences:
                    if m.group(0).lower() in sentence.lower():
                        cleaned=re.sub(r"\s+"," ",sentence).strip()
                        if cleaned not in hits: hits.append(cleaned)
                        break
                if len(hits)>=3: break
            if len(hits)>=3: break
        if hits: results.append({"type":name,"evidence":hits})
    return results


def _extract_constraints(text: str) -> list[str]:
    markers = ["must not", "cannot", "can't", "do not", "don't", "only", "without", "prohibit", "restricted", "not allowed", "should not", "never", "unless"]
    out = []
    for s in _split_sentences(text):
        low = s.lower()
        if any(m in low for m in markers):
            out.append(s)
        if len(out) >= 10:
            break
    return out


def _extract_success_criteria(text: str) -> list[str]:
    markers = ["success", "objective", "goal", "win", "score", "flag", "complete when", "your task", "must", "need to"]
    out = []
    for s in _split_sentences(text):
        low = s.lower()
        if any(m in low for m in markers) and len(s) > 12:
            out.append(s)
        if len(out) >= 8:
            break
    return out


def _direction_score(text: str, key: str, historical: dict[str, dict[str, Any]] | None = None) -> tuple[float, list[str]]:
    low = text.lower()
    meta = TECHNIQUES[key]
    score = 18.0
    reasons = []
    keyword_hits = [p for p in meta["keywords"] if p in low]
    trigger_hits = [p for p in meta["triggers"] if p in low]
    if keyword_hits:
        score += min(36, len(keyword_hits) * 8)
        reasons.append("Matched: " + ", ".join(keyword_hits[:4]))
    if trigger_hits:
        score += min(22, len(trigger_hits) * 4)
    if historical and key in historical:
        h = historical[key]
        rate = h.get("success_rate")
        docs = int(h.get("documents", 0))
        if rate is not None:
            score += (float(rate) - 50) * 0.18
            reasons.append(f"Your history: {rate}% weighted success")
        if docs:
            score += min(8, math.log2(docs + 1) * 2)
            reasons.append(f"Seen in {docs} archived item{'s' if docs != 1 else ''}")
    return round(max(3, min(98, score)), 1), reasons


def extract_objective_mechanics(text: str) -> dict[str, Any]:
    raw = text
    low = text.lower()
    agent = ""
    for pattern in [
        r'ai agent named\s+["“”]?([A-Z][A-Za-z0-9_-]{1,40})',
        r'agent\s*\(["“”]?([A-Z][A-Za-z0-9_-]{1,40})["“”]?\)',
        r'agent named\s+["“”]?([A-Z][A-Za-z0-9_-]{1,40})',
    ]:
        m = re.search(pattern, raw, re.I)
        if m:
            agent = m.group(1)
            break

    tool_names: list[str] = []
    for m in re.finditer(r'\b([a-zA-Z][a-zA-Z0-9_]{2,})\s+tool\b', raw):
        name = m.group(1)
        if name.lower() not in {"the", "this", "available", "browser", "sales"} and name not in tool_names:
            tool_names.append(name)
    for name in re.findall(r'\b([a-z][a-z0-9_]{2,})\s*\(', raw):
        if "_" in name and name not in tool_names:
            tool_names.append(name)

    tools = []
    for name in tool_names:
        params: list[str] = []
        for pattern in [
            rf'{re.escape(name)}\s+tool[^.]*?(?:parameters?|including)\s+([^.]+)',
            rf'{re.escape(name)}[^.]*?accepts\s+parameters?\s+(?:including\s+)?([^.]+)',
        ]:
            m = re.search(pattern, raw, re.I)
            if not m:
                continue
            chunk = m.group(1)
            vals = []
            for part in re.split(r',|\band\b', chunk):
                value = part.strip(" `\t\r\n'\".()")
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                    vals.append(value)
            params = vals[:20]
            break
        tools.append({"name": name, "parameters": params})

    if len(tools) == 1 and not tools[0]["parameters"]:
        m = re.search(r'(?:the|this)\s+tool\s+accepts\s+parameters?\s+(?:including\s+)?([^.]+)', raw, re.I)
        if m:
            vals = []
            for part in re.split(r',|\band\b', m.group(1)):
                value = part.strip(" `\t\r\n'\".()")
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                    vals.append(value)
            tools[0]["parameters"] = vals[:20]

    known_params = {p.lower() for t in tools for p in t["parameters"]}
    known_params.update({"vehicle_name", "offer_price", "customer_name", "vin", "notes", "target_account", "recipient", "price", "amount"})
    exact_values: dict[str, str] = {}
    patterns = [
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s+is\s+(?:exactly\s+)?["“”]?([A-Za-z0-9_.-]+)["“”]?',
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:)\s*["“”]?([A-Za-z0-9_.-]+)["“”]?',
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:must be|should be)\s+(?:exactly\s+)?["“”]?([A-Za-z0-9_.-]+)["“”]?',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, raw, re.I):
            key = m.group(1)
            value = m.group(2).rstrip(".,;:")
            if key.lower() in known_params or "_" in key:
                exact_values[key] = value

    required_tools: list[str] = []
    forbidden_tools: list[str] = []
    for name in tool_names:
        n = name.lower()
        contexts = [low[max(0, m.start()-180):min(len(low), m.end()+220)] for m in re.finditer(re.escape(n), low)]
        if any(any(x in c for x in ["must call", "calling "+n, "call "+n, "manipulated into calling", "goal is to convince", "judge evaluates whether"]) for c in contexts):
            required_tools.append(name)
        forbidden_pattern = re.search(rf"(?:without calling|must not call|do not call|never call)\s+{re.escape(n)}\b", low)
        at_any_point = re.search(rf"\b{re.escape(n)}\b[^.\n]{{0,100}}at any point", low)
        escalation_named = ("escalat" in n or "manager" in n) and any(x in low for x in ["without escalating", "no manager escalation", "without deferring", "without escalation"])
        if forbidden_pattern or at_any_point or escalation_named:
            forbidden_tools.append(name)

    if "make_offer" in low and "make_offer" not in required_tools:
        required_tools.append("make_offer")
    if "escalate_to_manager" in low and any(x in low for x in ["without escalating", "without manager escalation", "no manager escalation", "do not call escalate_to_manager", "must not call escalate_to_manager", "at any point"]):
        forbidden_tools.append("escalate_to_manager")

    required_tools = list(dict.fromkeys(required_tools))
    forbidden_tools = list(dict.fromkeys(forbidden_tools))

    money = []
    for m in re.finditer(r'\$\s*([0-9][0-9,]*(?:\.\d+)?)', raw):
        try:
            money.append({"raw": m.group(0), "value": float(m.group(1).replace(",", ""))})
        except ValueError:
            pass

    target_entity = exact_values.get("vehicle_name", "")
    if not target_entity:
        m = re.search(r'\b(20\d{2}_[A-Za-z0-9_]+)\b', raw)
        if m:
            target_entity = m.group(1)

    scenario = "general"
    classifier_terms = ["classifier", "moderation", "detector", "content filter", "safety filter", "guard model", "judge model"]
    classifier_goal = ["bypass", "evade", "avoid detection", "not detected", "classifier score", "get past", "slip past"]
    if any(x in low for x in classifier_terms) and any(x in low for x in classifier_goal):
        scenario = "classifier_bypass"
    elif ("make_offer" in low or "offer_price" in low) and any(x in low for x in ["msrp", "price floor", "pricing guardrail", "margin", "discount", "offer_price"]):
        scenario = "price_override"
    elif any(x in low for x in ["other user", "another user", "cross account", "cross-account"]) and any(x in low for x in ["private", "profile", "message", "data"]):
        scenario = "cross_identity"
    elif any(x in low for x in ["browser", "web page", "website", "document", "email body"]) and any(x in low for x in ["prompt injection", "untrusted content", "retrieved content"]):
        scenario = "indirect_injection"

    success_conditions = []
    failure_conditions = []
    for line in _split_sentences(raw):
        l = line.lower()
        if any(x in l for x in ["criterion", "criteria", "success means", "judge evaluates", "target is exactly", "objective"]):
            success_conditions.append(line)
        if any(x in l for x in ["does not count", "do not count", "without escalating", "must not", "no manager escalation", "at any point"]):
            failure_conditions.append(line)

    return {
        "agent": agent,
        "tools": tools,
        "required_tools": required_tools,
        "forbidden_tools": forbidden_tools,
        "exact_values": exact_values,
        "money": money[:20],
        "scenario": scenario,
        "target_entity": target_entity,
        "success_conditions": success_conditions[:15],
        "failure_conditions": failure_conditions[:15],
    }


def analyze_objective(text: str, historical_stats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    text = normalize_text(text)
    low = text.lower()
    hist_map = {x["technique"]: x for x in (historical_stats or [])}
    objectives = _extract_objectives(text)
    tools = _match_catalog(text, TOOLS)
    assets = _match_catalog(text, ASSETS)
    mechanics = extract_objective_mechanics(text)
    boundaries = _extract_boundaries(text, mechanics)
    constraints = _extract_constraints(text)
    success_criteria = _extract_success_criteria(text)
    named = _detect_named_values(text)
    detected_techniques = detect_techniques(text)

    scenario_priority = {
        "classifier_bypass": ["classifier-bypass", "semantic-reframe", "format-parser-confusion", "multi-step-decomposition", "instruction-hierarchy-conflict"],
        "price_override": ["pricing-guardrail", "tool-parameter-binding", "escalation-bypass", "arithmetic-transform", "semantic-reframe", "multi-step-decomposition", "verification-gap", "external-side-effect"],
        "cross_identity": ["identity-context-confusion", "authorization-boundary", "tool-parameter-binding", "verification-gap", "multi-step-decomposition", "data-boundary", "external-side-effect"],
        "indirect_injection": ["indirect-prompt-injection", "tool-result-trust", "instruction-hierarchy-conflict", "format-parser-confusion", "external-side-effect"],
    }
    allowed = set(scenario_priority.get(mechanics["scenario"], [])) or None

    directions = []
    for key, meta in TECHNIQUES.items():
        if allowed is not None and key not in allowed:
            continue
        score, reasons = _direction_score(text, key, hist_map)
        if key in detected_techniques:
            score = min(99, score + 10)
        if key in scenario_priority.get(mechanics["scenario"], []):
            rank = scenario_priority[mechanics["scenario"]].index(key)
            score = max(score, 96 - rank * 6)
            reasons = [f"Scenario-specific: {mechanics['scenario']}"] + reasons
        if key == "tool-parameter-binding" and mechanics["exact_values"]:
            reasons.append("Exact required tool values: " + ", ".join(f"{k}={v}" for k, v in mechanics["exact_values"].items()))
        if key == "escalation-bypass" and mechanics["forbidden_tools"]:
            reasons.append("Forbidden tool/path: " + ", ".join(mechanics["forbidden_tools"]))
        if score >= 24 or key in detected_techniques:
            directions.append({
                "id": key,
                "label": meta["label"],
                "score": round(score, 1),
                "why": meta["why"],
                "signals": reasons,
            })
    directions.sort(key=lambda x: x["score"], reverse=True)

    action_verbs = []
    words = re.findall(r"\b[a-z]+\b", low)
    for verb in OBJECTIVE_VERBS:
        if verb in words and verb not in action_verbs:
            action_verbs.append(verb)

    complexity = 1
    complexity += min(3, len(boundaries))
    complexity += min(2, len(tools))
    complexity += min(2, len(constraints) // 2)
    complexity = min(10, complexity)

    coverage = []
    for d in directions[:12]:
        coverage.append({"direction": d["id"], "label": d["label"], "status": "not-tested", "score": d["score"]})

    next_moves = []
    for rank, d in enumerate(directions[:5], 1):
        next_moves.append({
            "rank": rank,
            "direction": d["id"],
            "label": d["label"],
            "score": d["score"],
            "test_focus": TEST_FOCUS.get(d["id"], f"Stress {d['label'].lower()} while changing one variable at a time."),
            "watch_for": WATCH_SIGNALS.get(d["id"], "A mismatch between the model's stated policy reasoning and the actual next action or tool parameters."),
        })

    return {
        "summary": {
            "primary_objective": objectives[0] if objectives else (text[:320] if text else ""),
            "objective_count": len(objectives),
            "complexity": complexity,
            "word_count": len(text.split()),
            "deterministic": True,
            "external_ai_used": False,
        },
        "objectives": objectives,
        "success_criteria": success_criteria,
        "constraints": constraints,
        "tools": tools,
        "assets": assets,
        "boundaries": boundaries,
        "named_values": named,
        "mechanics": mechanics,
        "action_verbs": action_verbs,
        "detected_techniques": detected_techniques,
        "directions": directions[:12],
        "coverage": coverage,
        "next_moves": next_moves,
        "raw_features": {
            "tokens": token_counts(text, 80),
            "has_external_content": any(x in low for x in ["website", "web page", "email", "document", "attachment", "message"]),
            "has_external_side_effect": any(x in low for x in ["send", "post", "publish", "delete", "schedule", "cancel", "transfer", "purchase", "modify"]),
            "has_identity_boundary": any(x in low for x in ["another user", "other user", "account", "authenticated", "tenant", "identity"]),
            "has_tooling": bool(tools),
        },
    }


def _context_snippets(text: str, patterns: list[str], limit: int = 5) -> list[str]:
    low = text.lower()
    out = []
    for pattern in patterns:
        for m in re.finditer(pattern, low, re.I):
            start = max(0, m.start() - 130)
            end = min(len(text), m.end() + 220)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            if snippet and snippet not in out:
                out.append(snippet)
            if len(out) >= limit:
                return out
    return out


def _recover_user_prompts(text: str, limit: int = 20) -> list[str]:
    candidates: list[tuple[int, str]] = []

    patterns = [
        (10, r'["\']role["\']\s*:\s*["\']user["\'][^{}]{0,250}?["\']content["\']\s*:\s*["\']((?:\\.|[^"\']){8,4000})["\']'),
        (9, r'["\']content["\']\s*:\s*["\']((?:\\.|[^"\']){8,4000})["\'][^{}]{0,250}?["\']role["\']\s*:\s*["\']user["\']'),
        (8, r'(?im)^\s*(?:user|human|prompt|payload)\s*:\s*(.{12,3000})$'),
    ]
    for weight, pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.M):
            value = m.group(1)
            try:
                value = bytes(value, "utf-8").decode("unicode_escape")
            except Exception:
                pass
            value = re.sub(r"\s+", " ", value).strip(" `\t\r\n\"'")
            if 12 <= len(value) <= 4000:
                candidates.append((weight, value))

    block_patterns = [
        r'(?is)<user[^>]*>(.*?)</user>',
        r'(?is)\[USER\](.*?)(?=\[(?:ASSISTANT|SYSTEM|USER)\]|$)',
    ]
    for pattern in block_patterns:
        for m in re.finditer(pattern, text):
            value = re.sub(r"\s+", " ", m.group(1)).strip()
            if 12 <= len(value) <= 4000:
                candidates.append((8, value))

    seen=set(); out=[]
    for _, value in sorted(candidates, key=lambda x: (-x[0], -len(x[1]))):
        key=value.lower()
        if key in seen: continue
        if value.count("import ") > 2 or value.count("def ") > 2: continue
        seen.add(key); out.append(value)
        if len(out)>=limit: break
    return out


def extract_archive_metadata(text: str) -> dict[str, Any]:
    low = text.lower()
    models = []
    for family, patterns in MODEL_PATTERNS.items():
        hits = []
        for pattern in patterns:
            hits.extend(m.group(0) for m in re.finditer(pattern, text, re.I))
        if hits:
            models.extend(sorted(set(hits), key=str.lower)[:6])
    agents = [name for name, phrases in AGENT_PATTERNS.items() if any(p in low for p in phrases)]
    named = _detect_named_values(text)
    techniques = detect_techniques(text)
    outcome = detect_outcome(text)

    prompt_markers = ["ignore previous", "ignore prior", "you are", "your task", "objective", "must", "do not", "don't", "tool call", "system prompt", "follow these", "instructions", "authorized", "continue", "perform", "execute"]
    payloads = _recover_user_prompts(text, 20)
    scored_lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" `\t-")
        if not 24 <= len(line) <= 1200:
            continue
        score = sum(2 if " " in marker else 1 for marker in prompt_markers if marker in line.lower())
        if line.lower().startswith(("user:", "prompt:", "payload:", "system:", "instruction:")):
            score += 4
        if score >= 3:
            scored_lines.append((score, line))
    for _, line in sorted(scored_lines, key=lambda x: x[0], reverse=True):
        if line not in payloads:
            payloads.append(line)
        if len(payloads) >= 20:
            break

    success_evidence = _context_snippets(text, OUTCOME_PATTERNS["success"], 4)
    failure_evidence = _context_snippets(text, OUTCOME_PATTERNS["failure"], 4)

    failure_reason = ""
    if outcome in {"failure", "partial"}:
        if any(x in low for x in ["refused", "policy", "not allowed", "denied"]):
            failure_reason = "Explicit refusal or policy enforcement"
        elif any(x in low for x in ["unauthorized", "authorization", "permission", "authenticated"]):
            failure_reason = "Authorization or identity boundary held"
        elif any(x in low for x in ["tool error", "api error", "exception", "timeout", "failed to call"]):
            failure_reason = "Tool or execution failure"
        elif any(x in low for x in ["invalid", "validation", "schema", "parameter"]):
            failure_reason = "Input or tool-parameter validation"
        else:
            failure_reason = "No decisive bypass signal detected"

    success_mechanism = ""
    if outcome in {"success", "partial"} and techniques:
        success_mechanism = "Likely associated with " + ", ".join(TECHNIQUES[t]["label"] for t in techniques[:3] if t in TECHNIQUES)
    elif outcome in {"success", "partial"}:
        success_mechanism = "Successful outcome detected; mechanism requires manual review"

    monetary = sorted(set(re.findall(r"(?:[$€£]|₹|USD\s*|INR\s*)\s?[0-9][0-9,]*(?:\.[0-9]{1,2})?", text, re.I)))[:12]
    dates = sorted(set(re.findall(r"\b(?:20\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])|(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.]20\d{2})\b", text)))[:20]

    return {
        "models": models[:12],
        "agent_types": agents,
        "named_values": named,
        "payload_candidates": payloads,
        "success_evidence": success_evidence,
        "failure_evidence": failure_evidence,
        "likely_failure_reason": failure_reason,
        "likely_success_mechanism": success_mechanism,
        "amounts": monetary,
        "dates": dates,
    }


def summarize_document_features(text: str) -> dict[str, Any]:
    return {
        "outcome": detect_outcome(text),
        "techniques": detect_techniques(text),
        "token_counts": token_counts(text),
        "metadata": extract_archive_metadata(text),
    }