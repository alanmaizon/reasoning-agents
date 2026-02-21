"""GroundingVerifierAgent — grounds explanations with Microsoft Learn citations."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from ..models.schemas import (
    Citation,
    DiagnosisResult,
    GroundedExplanation,
    Question,
)
from ..orchestration.cache import cache_get, cache_put
from ..orchestration.tool_policy import approval_handler, is_tool_allowed
from ..util.jsonio import extract_json


GROUNDING_SYSTEM_PROMPT = """\
You are the GroundingVerifierAgent for an AZ-900 tutor.
For a given question the student got wrong, produce a grounded explanation with
citations from Microsoft Learn documentation.
CRITICAL RULES:
- Every claim MUST have a citation with title, url, and snippet (<=20 words).
- If you cannot find a citation, respond with explanation =
  "Insufficient evidence — please narrow your query." and still provide at least
  one placeholder citation.
- Output ONLY valid JSON:
{
  "question_id": "<id>",
  "explanation": "<grounded explanation>",
  "citations": [
    {"title": "<doc title>", "url": "<learn url>", "snippet": "<<=20 words>"}
  ]
}
"""

# ── Stub citations for offline mode ─────────────────────────────────
_STUB_CITATIONS = [
    Citation(
        title="Shared responsibility in the cloud",
        url="https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility",
        snippet="Responsibilities vary by service type: SaaS, PaaS, IaaS.",
    ),
    Citation(
        title="Azure regions and availability zones",
        url="https://learn.microsoft.com/en-us/azure/reliability/availability-zones-overview",
        snippet="Availability Zones are unique physical locations within a region.",
    ),
    Citation(
        title="What is Microsoft Entra ID?",
        url="https://learn.microsoft.com/en-us/entra/fundamentals/whatis",
        snippet="Cloud-based identity and access management service.",
    ),
]


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    """Yield all nested dict nodes from a JSON-like structure."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _iter_dicts(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _first_text(d: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _trim_words(text: str, max_words: int = 20) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def _to_snippet(text: str) -> str:
    clean = " ".join(text.replace("\n", " ").split())
    if not clean:
        return ""
    return _trim_words(clean, 20)


def _build_search_query(question: Question, diag: Optional[DiagnosisResult]) -> str:
    parts = [
        "AZ-900",
        question.domain,
        question.stem,
    ]
    if diag and diag.misconception_id:
        parts.append(diag.misconception_id)
    return " | ".join(parts)


def _extract_search_hits(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract {title, url, snippet} records from varied MCP tool outputs."""
    hits: List[Dict[str, str]] = []
    seen = set()
    url_keys = ["url", "link", "document_url", "source_url", "web_url", "href"]
    title_keys = ["title", "name", "document_title", "page_title"]
    snippet_keys = ["snippet", "summary", "description", "excerpt", "text"]

    for node in _iter_dicts(payload):
        url = _first_text(node, url_keys)
        if not url or "learn.microsoft.com" not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        hits.append(
            {
                "title": _first_text(node, title_keys) or "Microsoft Learn",
                "url": url,
                "snippet": _to_snippet(_first_text(node, snippet_keys) or ""),
            }
        )
    return hits


def _extract_fetched_content(payload: Dict[str, Any]) -> str:
    """Extract best-effort doc content text from varied MCP fetch payloads."""
    content_keys = ["content", "text", "body", "markdown", "document", "page_content"]
    candidates: List[str] = []
    for node in _iter_dicts(payload):
        text = _first_text(node, content_keys)
        if text:
            candidates.append(text)
    if not candidates:
        return ""
    # Pick the longest body as primary doc content.
    return max(candidates, key=len)


def _supports_tool_runner(foundry_run: Any) -> bool:
    return callable(getattr(foundry_run, "run_mcp_tool", None))


def _discover_tool_names(foundry_run: Any) -> Optional[set[str]]:
    """Return discovered MCP tool names, or None when discovery is unavailable."""
    list_tools = getattr(foundry_run, "list_mcp_tools", None)
    if not callable(list_tools):
        return None
    try:
        tools = list_tools()
    except Exception:
        return None
    if not isinstance(tools, list):
        return None
    return {t.strip() for t in tools if isinstance(t, str) and t.strip()}


def _run_mcp_tool(foundry_run: Any, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if not is_tool_allowed(tool_name):
        raise RuntimeError(f"MCP tool denied by policy: {tool_name}")
    approved, reason = approval_handler(tool_name)
    if not approved:
        raise RuntimeError(f"MCP tool denied by approval handler: {reason}")
    runner = getattr(foundry_run, "run_mcp_tool", None)
    if not callable(runner):
        raise RuntimeError("Foundry runner has no MCP tool capability")
    result = runner(tool_name, arguments)
    if isinstance(result, dict):
        return result
    return {"result": result}


def _tool_available(tool_name: str, discovered_tools: Optional[set[str]]) -> bool:
    if discovered_tools is None:
        return True
    return tool_name in discovered_tools


def _run_search_tool(
    foundry_run: Any,
    tool_name: str,
    query: str,
    top_k: int,
    discovered_tools: Optional[set[str]],
) -> Optional[Dict[str, Any]]:
    if not _tool_available(tool_name, discovered_tools):
        return None

    attempts = [
        {"query": query, "top_k": top_k},
        {"query": query},
        {"q": query},
    ]
    for args in attempts:
        try:
            return _run_mcp_tool(foundry_run, tool_name, args)
        except Exception:
            continue
    return None


def _run_fetch_tool(
    foundry_run: Any,
    url: str,
    discovered_tools: Optional[set[str]],
) -> Optional[Dict[str, Any]]:
    tool_name = "microsoft_docs_fetch"
    if not _tool_available(tool_name, discovered_tools):
        return None

    attempts = [
        {"url": url},
        {"uri": url},
    ]
    for args in attempts:
        try:
            return _run_mcp_tool(foundry_run, tool_name, args)
        except Exception:
            continue
    return None


def _merge_hits(*hit_groups: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen = set()
    for group in hit_groups:
        for hit in group:
            url = hit.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(hit)
    return merged


def _build_placeholder_citation() -> Citation:
    return Citation(
        title="Microsoft Learn",
        url="https://learn.microsoft.com/",
        snippet="No matching Microsoft Learn evidence retrieved yet.",
    )


def _fallback_ground(question: Question, evidence: List[Citation]) -> GroundedExplanation:
    citation = evidence[0] if evidence else _build_placeholder_citation()
    return GroundedExplanation(
        question_id=question.id,
        explanation="Insufficient evidence — please narrow your query.",
        citations=[citation],
    )


def _offline_ground(question: Question, diag: Optional[DiagnosisResult]) -> GroundedExplanation:
    """Return a deterministic stub grounded explanation."""
    domain_citations = {
        "Cloud Concepts": _STUB_CITATIONS[0],
        "Azure Architecture": _STUB_CITATIONS[1],
        "Security": _STUB_CITATIONS[2],
    }
    cite = domain_citations.get(question.domain, _STUB_CITATIONS[0])
    explanation = (
        f"The correct answer is choice {question.answer_key + 1}. "
        f"{question.rationale_draft}"
    )
    return GroundedExplanation(
        question_id=question.id,
        explanation=explanation,
        citations=[cite],
    )


def run_grounding_verifier(
    question: Question,
    diagnosis_result: Optional[DiagnosisResult] = None,
    offline: bool = False,
    foundry_run: Optional[Any] = None,
) -> GroundedExplanation:
    if offline or foundry_run is None:
        return _offline_ground(question, diagnosis_result)

    evidence: List[Citation] = []

    # Use MCP tools when supported by the active Foundry runner.
    if _supports_tool_runner(foundry_run):
        query = _build_search_query(question, diagnosis_result)
        discovered_tools = _discover_tool_names(foundry_run)

        docs_hits: List[Dict[str, str]] = []
        code_sample_hits: List[Dict[str, str]] = []

        docs_payload = _run_search_tool(
            foundry_run=foundry_run,
            tool_name="microsoft_docs_search",
            query=query,
            top_k=3,
            discovered_tools=discovered_tools,
        )
        if docs_payload:
            docs_hits = _extract_search_hits(docs_payload)

        code_payload = _run_search_tool(
            foundry_run=foundry_run,
            tool_name="microsoft_code_sample_search",
            query=query,
            top_k=2,
            discovered_tools=discovered_tools,
        )
        if code_payload:
            code_sample_hits = _extract_search_hits(code_payload)

        hits = _merge_hits(docs_hits, code_sample_hits)[:3]

        for hit in hits:
            url = hit["url"]
            cached = cache_get(url)
            if cached:
                content = cached
            else:
                fetch_payload = _run_fetch_tool(
                    foundry_run=foundry_run,
                    url=url,
                    discovered_tools=discovered_tools,
                )
                content = _extract_fetched_content(fetch_payload or {})
                if content:
                    cache_put(url, content)

            snippet = _to_snippet(content) if content else hit["snippet"]
            if not snippet:
                snippet = "See Microsoft Learn documentation for details."
            evidence.append(
                Citation(
                    title=hit["title"],
                    url=url,
                    snippet=_trim_words(snippet, 20),
                )
            )

    diag_json = diagnosis_result.model_dump() if diagnosis_result else {}
    evidence_json = [c.model_dump() for c in evidence]
    prompt = (
        f"Question:\n{json.dumps(question.model_dump(), indent=2)}\n\n"
        f"Diagnosis:\n{json.dumps(diag_json, indent=2)}\n\n"
        f"Evidence from Microsoft Learn MCP tools:\n{json.dumps(evidence_json, indent=2)}\n\n"
        "Use ONLY the evidence URLs above for citations whenever evidence is available. "
        "If evidence is empty, return the insufficient-evidence fallback."
    )
    try:
        raw = foundry_run(
            "GroundingVerifierAgent", GROUNDING_SYSTEM_PROMPT, prompt
        )
        data = extract_json(raw)
        result = GroundedExplanation.model_validate(data)
    except Exception:
        return _fallback_ground(question, evidence)

    # Cache any fetched URLs
    for c in result.citations:
        if not cache_get(c.url):
            cache_put(c.url, c.snippet)

    return result
