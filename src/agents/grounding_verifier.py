"""GroundingVerifierAgent — grounds explanations with Microsoft Learn citations."""

from __future__ import annotations

import json
import logging
import re
from time import perf_counter
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

_grounding_logger = logging.getLogger("mdt.grounding")


GROUNDING_SYSTEM_PROMPT = """\
You are the GroundingVerifierAgent for an AZ-900 tutor.
For a given question the student got wrong, produce a grounded explanation with
citations from Microsoft Learn documentation.
CRITICAL RULES:
- Every claim MUST have a citation with title, url, and snippet (<=20 words).
- Explain the correct option directly and use AZ-900 terminology.
- Prefer concrete wording over generic feedback.
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
_DOMAIN_FALLBACK_CITATIONS = {
    "Cloud Concepts": _STUB_CITATIONS[0],
    "Azure Architecture": _STUB_CITATIONS[1],
    "Security": _STUB_CITATIONS[2],
    "Identity": Citation(
        title="What is Microsoft Entra ID?",
        url="https://learn.microsoft.com/en-us/entra/fundamentals/whatis",
        snippet="Entra ID provides identity and access management for Azure resources.",
    ),
    "Azure Services": Citation(
        title="Overview of Azure App Service",
        url="https://learn.microsoft.com/en-us/azure/app-service/overview",
        snippet="Azure App Service hosts web apps and APIs as a managed platform.",
    ),
    "Governance": Citation(
        title="What is Azure Policy?",
        url="https://learn.microsoft.com/en-us/azure/governance/policy/overview",
        snippet="Azure Policy enforces standards and assesses compliance across resources.",
    ),
    "Cost Management": Citation(
        title="Cost Management + Billing documentation",
        url="https://learn.microsoft.com/en-us/azure/cost-management-billing/",
        snippet="Use cost analysis and budgets to monitor and optimize Azure spend.",
    ),
}
_CHOICE_PREFIX = re.compile(r"^[A-F]\)\s*")


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


def _choice_text(question: Question, index: int) -> str:
    if index < 0 or index >= len(question.choices):
        return "Unknown"
    clean = " ".join(str(question.choices[index]).split()).strip()
    clean = _CHOICE_PREFIX.sub("", clean).strip()
    return clean or "Unknown"


def _choice_ref(question: Question, index: int) -> str:
    label = chr(ord("A") + index)
    return f"{label}) {_choice_text(question, index)}"


def _build_search_queries(question: Question, diag: Optional[DiagnosisResult]) -> List[str]:
    stem = " ".join(question.stem.split())
    correct_choice = _choice_text(question, question.answer_key)
    queries: List[str] = [
        f"AZ-900 {question.domain} {stem}",
        f"AZ-900 {question.domain} {correct_choice}",
        f"Microsoft Learn {correct_choice} Azure",
    ]
    if diag and diag.misconception_id:
        queries.append(f"AZ-900 {question.domain} {diag.misconception_id}")

    deduped: List[str] = []
    seen = set()
    for query in queries:
        key = query.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


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


def _is_placeholder_citation(citation: Citation) -> bool:
    url = citation.url.rstrip("/")
    if url == "https://learn.microsoft.com":
        return True
    return "no matching microsoft learn evidence retrieved yet" in citation.snippet.lower()


def _fallback_citations(question: Question, evidence: List[Citation]) -> List[Citation]:
    real_evidence = [c for c in evidence if not _is_placeholder_citation(c)]
    if real_evidence:
        return real_evidence[:2]
    citation = _DOMAIN_FALLBACK_CITATIONS.get(question.domain)
    if citation:
        return [citation]
    return [_build_placeholder_citation()]


def _deterministic_explanation(
    question: Question,
    diag: Optional[DiagnosisResult],
) -> str:
    correct = _choice_ref(question, question.answer_key)
    rationale = " ".join(question.rationale_draft.split()).strip()
    if not rationale:
        rationale = "Review this AZ-900 concept and why this option best fits the service model."
    if not rationale.endswith((".", "!", "?")):
        rationale = f"{rationale}."
    if diag and diag.misconception_id:
        return f"Correct answer: {correct}. {rationale} Focus area: {diag.misconception_id}."
    return f"Correct answer: {correct}. {rationale}"


def _fallback_ground(
    question: Question,
    evidence: List[Citation],
    diag: Optional[DiagnosisResult],
) -> GroundedExplanation:
    citations = _fallback_citations(question, evidence)
    placeholder_only = (
        len(citations) == 1 and citations[0].url.rstrip("/") == "https://learn.microsoft.com"
    )
    explanation = (
        "Insufficient evidence — please narrow your query."
        if placeholder_only
        else _deterministic_explanation(question, diag)
    )
    return GroundedExplanation(
        question_id=question.id,
        explanation=explanation,
        citations=citations,
    )


def _offline_ground(question: Question, diag: Optional[DiagnosisResult]) -> GroundedExplanation:
    """Return a deterministic stub grounded explanation."""
    citations = _fallback_citations(question, [])
    explanation = _deterministic_explanation(question, diag)
    return GroundedExplanation(
        question_id=question.id,
        explanation=explanation,
        citations=citations,
    )


def _is_low_signal_explanation(text: str) -> bool:
    clean = " ".join(str(text).split()).strip()
    if len(clean) < 30:
        return True
    return clean.lower().startswith("insufficient evidence")


def _short_error(exc: Exception, max_len: int = 240) -> str:
    text = " ".join(str(exc).split())
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3].rstrip()}..."


def run_grounding_verifier(
    question: Question,
    diagnosis_result: Optional[DiagnosisResult] = None,
    offline: bool = False,
    foundry_run: Optional[Any] = None,
) -> GroundedExplanation:
    started = perf_counter()
    _grounding_logger.info(
        "grounding_started",
        extra={
            "event": "grounding_started",
            "question_id": question.id,
            "domain": question.domain,
            "offline_requested": offline,
            "has_foundry_runner": foundry_run is not None,
        },
    )
    if offline or foundry_run is None:
        result = _offline_ground(question, diagnosis_result)
        _grounding_logger.info(
            "grounding_completed",
            extra={
                "event": "grounding_completed",
                "question_id": question.id,
                "domain": question.domain,
                "mode": "offline_stub",
                "evidence_count": 0,
                "citations_count": len(result.citations),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return result

    evidence: List[Citation] = []

    # Use MCP tools when supported by the active Foundry runner.
    if _supports_tool_runner(foundry_run):
        queries = _build_search_queries(question, diagnosis_result)
        discovered_tools = _discover_tool_names(foundry_run)
        _grounding_logger.info(
            "grounding_mcp_discovery",
            extra={
                "event": "grounding_mcp_discovery",
                "question_id": question.id,
                "domain": question.domain,
                "discovery_available": discovered_tools is not None,
                "discovered_tools_count": (
                    len(discovered_tools) if discovered_tools is not None else None
                ),
                "query_count": len(queries),
            },
        )

        docs_hits: List[Dict[str, str]] = []
        code_sample_hits: List[Dict[str, str]] = []

        for idx, query in enumerate(queries, start=1):
            search_started = perf_counter()
            docs_payload = _run_search_tool(
                foundry_run=foundry_run,
                tool_name="microsoft_docs_search",
                query=query,
                top_k=3,
                discovered_tools=discovered_tools,
            )
            before = len(docs_hits)
            if docs_payload:
                docs_hits = _merge_hits(docs_hits, _extract_search_hits(docs_payload))
            _grounding_logger.info(
                "grounding_mcp_search",
                extra={
                    "event": "grounding_mcp_search",
                    "question_id": question.id,
                    "tool_name": "microsoft_docs_search",
                    "query_index": idx,
                    "query_length": len(query),
                    "payload_received": bool(docs_payload),
                    "hits_added": len(docs_hits) - before,
                    "hits_total": len(docs_hits),
                    "latency_ms": round((perf_counter() - search_started) * 1000, 2),
                },
            )
            if len(docs_hits) >= 3:
                break

        for idx, query in enumerate(queries[:2], start=1):
            search_started = perf_counter()
            code_payload = _run_search_tool(
                foundry_run=foundry_run,
                tool_name="microsoft_code_sample_search",
                query=query,
                top_k=2,
                discovered_tools=discovered_tools,
            )
            before = len(code_sample_hits)
            if code_payload:
                code_sample_hits = _merge_hits(
                    code_sample_hits,
                    _extract_search_hits(code_payload),
                )
            _grounding_logger.info(
                "grounding_mcp_search",
                extra={
                    "event": "grounding_mcp_search",
                    "question_id": question.id,
                    "tool_name": "microsoft_code_sample_search",
                    "query_index": idx,
                    "query_length": len(query),
                    "payload_received": bool(code_payload),
                    "hits_added": len(code_sample_hits) - before,
                    "hits_total": len(code_sample_hits),
                    "latency_ms": round((perf_counter() - search_started) * 1000, 2),
                },
            )
            if len(code_sample_hits) >= 2:
                break

        hits = _merge_hits(docs_hits, code_sample_hits)[:3]
        _grounding_logger.info(
            "grounding_mcp_hits_selected",
            extra={
                "event": "grounding_mcp_hits_selected",
                "question_id": question.id,
                "docs_hits": len(docs_hits),
                "code_sample_hits": len(code_sample_hits),
                "selected_hits": len(hits),
            },
        )

        for hit in hits:
            url = hit["url"]
            cached = cache_get(url)
            fetch_started = perf_counter()
            fetch_payload_received = False
            if cached:
                content = cached
            else:
                fetch_payload = _run_fetch_tool(
                    foundry_run=foundry_run,
                    url=url,
                    discovered_tools=discovered_tools,
                )
                fetch_payload_received = bool(fetch_payload)
                content = _extract_fetched_content(fetch_payload or {})
                if content:
                    cache_put(url, content)
            _grounding_logger.info(
                "grounding_mcp_fetch",
                extra={
                    "event": "grounding_mcp_fetch",
                    "question_id": question.id,
                    "url": url,
                    "cache_hit": bool(cached),
                    "fetch_payload_received": fetch_payload_received,
                    "content_length": len(content),
                    "latency_ms": round((perf_counter() - fetch_started) * 1000, 2),
                },
            )

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
    else:
        _grounding_logger.warning(
            "grounding_mcp_unavailable",
            extra={
                "event": "grounding_mcp_unavailable",
                "question_id": question.id,
                "domain": question.domain,
                "reason": "foundry_runner_without_tool_capability",
            },
        )

    _grounding_logger.info(
        "grounding_evidence_ready",
        extra={
            "event": "grounding_evidence_ready",
            "question_id": question.id,
            "evidence_count": len(evidence),
        },
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
        model_started = perf_counter()
        raw = foundry_run(
            "GroundingVerifierAgent", GROUNDING_SYSTEM_PROMPT, prompt
        )
        data = extract_json(raw)
        result = GroundedExplanation.model_validate(data)
        _grounding_logger.info(
            "grounding_model_output_valid",
            extra={
                "event": "grounding_model_output_valid",
                "question_id": question.id,
                "citations_count": len(result.citations),
                "latency_ms": round((perf_counter() - model_started) * 1000, 2),
            },
        )
    except Exception as exc:
        fallback = _fallback_ground(question, evidence, diagnosis_result)
        _grounding_logger.warning(
            "grounding_fallback_used",
            extra={
                "event": "grounding_fallback_used",
                "question_id": question.id,
                "domain": question.domain,
                "fallback_reason": "model_exception_or_invalid_json",
                "error": _short_error(exc),
                "evidence_count": len(evidence),
                "citations_count": len(fallback.citations),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return fallback

    if _is_low_signal_explanation(result.explanation):
        fallback = _fallback_ground(question, result.citations or evidence, diagnosis_result)
        _grounding_logger.warning(
            "grounding_fallback_used",
            extra={
                "event": "grounding_fallback_used",
                "question_id": question.id,
                "domain": question.domain,
                "fallback_reason": "low_signal_explanation",
                "explanation_length": len(result.explanation or ""),
                "evidence_count": len(evidence),
                "citations_count": len(fallback.citations),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return fallback

    # Cache any fetched URLs
    for c in result.citations:
        if not cache_get(c.url):
            cache_put(c.url, c.snippet)

    _grounding_logger.info(
        "grounding_completed",
        extra={
            "event": "grounding_completed",
            "question_id": question.id,
            "domain": question.domain,
            "mode": "online",
            "evidence_count": len(evidence),
            "citations_count": len(result.citations),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        },
    )
    return result
