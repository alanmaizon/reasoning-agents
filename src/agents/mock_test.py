"""Deterministic + randomized AZ-900 mock test assets."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Dict, List, Sequence

from ..models.schemas import Exam, Plan, Question


MOCK_MIN_QUESTIONS = 40
MOCK_MAX_QUESTIONS = 60
_CHOICE_LABELS = ("A", "B", "C", "D")


@dataclass(frozen=True)
class ConceptCard:
    domain: str
    term: str
    definition: str
    rationale: str


_CARDS: List[ConceptCard] = [
    ConceptCard(
        domain="Cloud Concepts",
        term="Shared responsibility model",
        definition="A model where Microsoft and the customer split security and compliance responsibilities.",
        rationale="Security ownership changes by service type; responsibilities are shared, not transferred.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Consumption-based model",
        definition="A billing model where organizations pay only for resources they use.",
        rationale="Consumption pricing aligns costs with actual usage.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Public cloud",
        definition="Cloud resources provided over the internet and shared across multiple tenants.",
        rationale="Public cloud emphasizes elasticity and provider-managed infrastructure.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Private cloud",
        definition="Cloud resources dedicated to a single organization.",
        rationale="Private cloud prioritizes dedicated control and isolation.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Hybrid cloud",
        definition="An approach that combines on-premises, private cloud, and public cloud resources.",
        rationale="Hybrid strategies mix environments to meet business and compliance needs.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="IaaS",
        definition="A cloud service model where customers manage operating systems and applications on provider infrastructure.",
        rationale="IaaS provides the most infrastructure control among major cloud service models.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="PaaS",
        definition="A cloud service model where the provider manages platform components and the customer focuses on applications.",
        rationale="PaaS reduces platform management overhead for developers.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="SaaS",
        definition="A cloud service model where complete applications are delivered over the internet.",
        rationale="SaaS offloads nearly all infrastructure and platform management to the provider.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Serverless",
        definition="A cloud execution model where infrastructure is abstracted and billing is typically event- or execution-based.",
        rationale="Serverless minimizes infrastructure management and scales automatically.",
    ),
    ConceptCard(
        domain="Cloud Concepts",
        term="Scalability",
        definition="The ability to handle increased workload by adding or removing resources.",
        rationale="Scalability is a key cloud benefit for handling variable demand.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Azure region",
        definition="A set of datacenters deployed within a specific geographic area.",
        rationale="Regions are foundational units for resource deployment and latency planning.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Availability Zone",
        definition="A physically separate location inside an Azure region designed for high availability.",
        rationale="Zones increase resiliency against datacenter-level failures.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Region pair",
        definition="Two Azure regions in the same geography paired for disaster recovery and platform updates.",
        rationale="Region pairs support business continuity and recovery planning.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Resource group",
        definition="A logical container for managing Azure resources that share lifecycle and governance settings.",
        rationale="Resource groups organize related resources for deployment and management.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Subscription",
        definition="An agreement and boundary for billing, access control, and resource quotas in Azure.",
        rationale="Subscriptions are billing and governance scopes, not physical infrastructure.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Management group",
        definition="A scope above subscriptions used to apply governance policies at scale.",
        rationale="Management groups enable hierarchical governance across subscriptions.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="Azure Virtual Network",
        definition="A service that enables private networking between Azure resources and hybrid environments.",
        rationale="Virtual networks provide network isolation and connectivity in Azure.",
    ),
    ConceptCard(
        domain="Azure Architecture",
        term="ExpressRoute",
        definition="A private connection service between on-premises networks and Microsoft cloud services.",
        rationale="ExpressRoute avoids traversing the public internet for enterprise connectivity.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure Virtual Machines",
        definition="On-demand Windows or Linux servers in Azure for IaaS workloads.",
        rationale="Virtual Machines provide flexible compute with full OS-level control.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Virtual Machine Scale Sets",
        definition="A service for deploying and managing a group of load-balanced VMs that scale automatically.",
        rationale="Scale sets are used for high-scale, resilient VM workloads.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure Web Apps",
        definition="A managed application hosting service for web apps and APIs.",
        rationale="Web Apps provide managed hosting without VM administration.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure Functions",
        definition="An event-driven compute service for running small pieces of code without server management.",
        rationale="Functions are used for serverless execution patterns.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure DNS",
        definition="A hosting service for DNS domains using Azure infrastructure.",
        rationale="Azure DNS provides name resolution with Azure-integrated management.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure VPN Gateway",
        definition="A service for encrypted connectivity between Azure virtual networks and on-premises sites.",
        rationale="VPN Gateway enables secure hybrid network connectivity.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure Storage account",
        definition="A namespace that contains data services like blobs, files, queues, and tables.",
        rationale="Storage accounts are the top-level container for Azure Storage services.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Azure Key Vault",
        definition="A managed service for securely storing secrets, keys, and certificates.",
        rationale="Key Vault centralizes sensitive secret and key management.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Private endpoint",
        definition="A network interface that connects a service privately to a virtual network.",
        rationale="Private endpoints keep service traffic on private IP space.",
    ),
    ConceptCard(
        domain="Azure Services",
        term="Public endpoint",
        definition="An externally accessible service endpoint reachable over the public internet.",
        rationale="Public endpoints enable internet-facing access when required.",
    ),
    ConceptCard(
        domain="Identity",
        term="Microsoft Entra ID",
        definition="Microsoft's cloud identity and access management service.",
        rationale="Entra ID handles authentication and identity-based authorization in Azure.",
    ),
    ConceptCard(
        domain="Identity",
        term="Multifactor authentication (MFA)",
        definition="An authentication method requiring more than one verification factor.",
        rationale="MFA reduces account compromise risk from leaked passwords.",
    ),
    ConceptCard(
        domain="Identity",
        term="Conditional Access",
        definition="A policy engine that enforces access decisions based on conditions like user, device, and location.",
        rationale="Conditional Access applies adaptive controls to sign-in events.",
    ),
    ConceptCard(
        domain="Identity",
        term="Role-Based Access Control (RBAC)",
        definition="An authorization model assigning permissions through roles at a defined scope.",
        rationale="RBAC enforces least privilege by scoping permissions to roles and resources.",
    ),
    ConceptCard(
        domain="Security",
        term="Zero Trust",
        definition="A security strategy that assumes breach and continuously verifies every access request.",
        rationale="Zero Trust reduces implicit trust and enforces explicit verification.",
    ),
    ConceptCard(
        domain="Security",
        term="Microsoft Defender for Cloud",
        definition="A cloud security posture and workload protection service for Azure and hybrid resources.",
        rationale="Defender for Cloud provides security recommendations and protection capabilities.",
    ),
    ConceptCard(
        domain="Security",
        term="Network Security Group (NSG)",
        definition="A filtering control that allows or denies network traffic to Azure resources.",
        rationale="NSGs enforce network traffic rules at subnet or NIC level.",
    ),
    ConceptCard(
        domain="Security",
        term="Defense in depth",
        definition="A layered security approach using multiple controls across identity, network, compute, and data.",
        rationale="Layered controls reduce single-point security failures.",
    ),
    ConceptCard(
        domain="Governance",
        term="Azure Policy",
        definition="A governance service that defines and enforces standards for resources.",
        rationale="Azure Policy is used to audit or enforce compliance at scale.",
    ),
    ConceptCard(
        domain="Governance",
        term="Resource lock",
        definition="A setting that prevents accidental deletion or modification of Azure resources.",
        rationale="Locks protect critical resources from unintended changes.",
    ),
    ConceptCard(
        domain="Governance",
        term="Tag",
        definition="A name-value pair attached to Azure resources for organization and cost reporting.",
        rationale="Tags improve governance, organization, and chargeback visibility.",
    ),
    ConceptCard(
        domain="Governance",
        term="Azure Arc",
        definition="A service for managing and governing resources across on-premises, multi-cloud, and edge environments.",
        rationale="Azure Arc extends Azure management beyond Azure-hosted resources.",
    ),
    ConceptCard(
        domain="Cost Management",
        term="Azure Cost Management and Billing",
        definition="A service for analyzing costs, setting budgets, and tracking cloud spend.",
        rationale="Cost Management helps monitor and optimize Azure spending.",
    ),
    ConceptCard(
        domain="Cost Management",
        term="Pricing calculator",
        definition="A tool used to estimate Azure solution costs before deployment.",
        rationale="Pricing Calculator supports pre-deployment cost forecasting.",
    ),
    ConceptCard(
        domain="Cost Management",
        term="Azure Advisor",
        definition="A recommendation service that suggests improvements for reliability, security, performance, cost, and operations.",
        rationale="Advisor provides optimization guidance across multiple pillars, including cost.",
    ),
    ConceptCard(
        domain="Cost Management",
        term="Reserved capacity",
        definition="A purchasing option that reduces cost for predictable workloads by committing for a term.",
        rationale="Reservations can reduce costs when usage is stable and predictable.",
    ),
]

_DOMAIN_TO_MISCONCEPTION = {
    "Cloud Concepts": "SRM",
    "Azure Architecture": "REGION",
    "Security": "SEC",
    "Cost Management": "PRICING",
    "Governance": "GOV",
    "Identity": "IDAM",
    "Azure Services": "SERVICE_SCOPE",
}


def _pick_distractor_indexes(index: int, pool_size: int) -> List[int]:
    offsets = (3, 7, 11, 17, 23, 29, 31)
    picks: List[int] = []
    for offset in offsets:
        candidate = (index + offset) % pool_size
        if candidate == index or candidate in picks:
            continue
        picks.append(candidate)
        if len(picks) == 3:
            break
    if len(picks) != 3:
        raise RuntimeError("Unable to build distractors for mock test bank.")
    return picks


def _label_choices(values: Sequence[str]) -> List[str]:
    if len(values) != 4:
        raise ValueError("Exactly 4 choices are required.")
    return [f"{_CHOICE_LABELS[i]}) {value}" for i, value in enumerate(values)]


def _positioned_choices(
    correct: str,
    distractors: Sequence[str],
    correct_index: int,
) -> tuple[List[str], int]:
    if len(distractors) < 3:
        raise ValueError("At least 3 distractors are required.")
    slots: List[str] = [""] * 4
    slots[correct_index] = correct
    cursor = 0
    for idx in range(4):
        if idx == correct_index:
            continue
        slots[idx] = distractors[cursor]
        cursor += 1
    return slots, correct_index


def _build_question_bank(cards: Sequence[ConceptCard]) -> List[Question]:
    bank: List[Question] = []
    pool_size = len(cards)

    for idx, card in enumerate(cards):
        distractor_cards = [cards[i] for i in _pick_distractor_indexes(idx, pool_size)]

        term_choices, term_answer_key = _positioned_choices(
            card.term,
            [c.term for c in distractor_cards],
            correct_index=idx % 4,
        )
        bank.append(
            Question(
                id=f"drop-{idx + 1}",
                domain=card.domain,
                stem=(
                    "An example of [Dropdown Menu] is "
                    f"{card.definition}"
                ),
                choices=list(term_choices),
                answer_key=term_answer_key,
                rationale_draft=card.rationale,
            )
        )

        def_choices, def_answer_key = _positioned_choices(
            card.definition,
            [c.definition for c in distractor_cards],
            correct_index=(idx + 1) % 4,
        )
        bank.append(
            Question(
                id=f"def-{idx + 1}",
                domain=card.domain,
                stem=f"What is the primary purpose of {card.term}?",
                choices=_label_choices(def_choices),
                answer_key=def_answer_key,
                rationale_draft=card.rationale,
            )
        )

    return bank


_QUESTION_BANK = _build_question_bank(_CARDS)

if len(_QUESTION_BANK) < MOCK_MAX_QUESTIONS:
    raise RuntimeError(
        f"Mock question bank size {len(_QUESTION_BANK)} is below required "
        f"maximum sample size {MOCK_MAX_QUESTIONS}."
    )


def build_mock_test_session() -> tuple[Plan, Exam]:
    """Build a randomized AZ-900-like mock session with 40-60 questions."""
    rng = random.SystemRandom()
    question_count = rng.randint(MOCK_MIN_QUESTIONS, MOCK_MAX_QUESTIONS)
    sampled = rng.sample(_QUESTION_BANK, k=question_count)

    questions = [
        question.model_copy(update={"id": str(i + 1)})
        for i, question in enumerate(sampled)
    ]

    domain_counts: Dict[str, int] = {}
    for question in questions:
        domain_counts[question.domain] = domain_counts.get(question.domain, 0) + 1

    domains = sorted(domain_counts, key=domain_counts.get, reverse=True)
    weights = {
        domain: round(count / question_count, 3)
        for domain, count in domain_counts.items()
    }

    next_focus: List[str] = []
    for domain in domains:
        mid = _DOMAIN_TO_MISCONCEPTION.get(domain)
        if not mid or mid in next_focus:
            continue
        next_focus.append(mid)
        if len(next_focus) == 3:
            break
    if not next_focus:
        next_focus = ["TERMS"]

    plan = Plan(
        domains=domains,
        weights=weights,
        target_questions=question_count,
        next_focus=next_focus,
    )
    exam = Exam(questions=questions)
    return plan, exam
