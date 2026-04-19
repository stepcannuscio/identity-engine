"""Canonical guided-interview domain and question catalog."""

from __future__ import annotations

DOMAINS = [
    {
        "name": "personality",
        "description": "Core personality traits, thinking styles, and behavioral defaults.",
        "questions": [
            "How do you recharge after a demanding day or week?",
            "Walk me through how you typically make an important decision.",
            "How do you respond when you don't have enough information to act?",
            "What does conflict look like for you — how do you handle it?",
            "Describe your ideal working conditions.",
            "How do you respond to critical feedback?",
            "What kind of work puts you in a state of flow most reliably?",
        ],
    },
    {
        "name": "values",
        "description": "Deeply held values, ethical commitments, and non-negotiables.",
        "questions": [
            "What are the two or three things you would never compromise on?",
            "What does integrity mean to you in day-to-day terms?",
            "How do you think about money — what role does it play in your life?",
            "What does a life well-lived look like to you?",
        ],
    },
    {
        "name": "goals",
        "description": "Short-term and long-term goals, aspirations, and active letting-go.",
        "questions": [
            "What is the most important thing you are trying to achieve in the next six months,"
            " professionally?",
            "What is the most important thing you are trying to achieve in the next six months,"
            " personally?",
            "What does success look like to you right now — not abstractly, but concretely?",
            "What are you actively trying to stop doing or let go of?",
        ],
    },
    {
        "name": "patterns",
        "description": "Recurring behavioral patterns, habits, and tendencies.",
        "questions": [
            "When are you most productive during the day, and what does that look like?",
            "What does procrastination look like for you specifically?",
            "How do you behave when you are under significant stress?",
            "What pulls you off track most reliably?",
            "How do you learn new things best?",
        ],
    },
    {
        "name": "voice",
        "description": "Communication style, tone, and self-expression.",
        "questions": [
            "How would you describe your communication style to someone who has never met you?",
            "How does the way you write or speak change between professional and personal"
            " contexts?",
            "What tone do you default to when you are most yourself?",
            "What usually makes a draft sound most like you instead of generic?",
            "What kinds of phrasing, tone, or polish levels never feel like you?",
            "When your writing is working, is it more blunt, warm, calm, playful, or something else?",
        ],
    },
    {
        "name": "relationships",
        "description": "Attitudes, needs, and patterns around relationships.",
        "questions": [
            "What do you need most from close friendships?",
            "How do you show care for people you are close to?",
            "What causes you to pull back from someone?",
            "How is trust built and broken for you?",
        ],
    },
    {
        "name": "fears",
        "description": "Fears, anxieties, and avoidance patterns.",
        "questions": [
            "What does professional failure look like in your head?",
            "What are you most afraid staying the same would mean?",
            "What do you most not want people to think about you?",
        ],
    },
    {
        "name": "beliefs",
        "description": "Beliefs about the world, work, and self.",
        "questions": [
            "What do you believe separates good engineers from great ones?",
            "How do you think about the role of luck versus effort in outcomes?",
            "What do you believe about privacy in the modern world?",
            "Where do you think software engineering is headed, and what does that mean for you?",
        ],
    },
]

DOMAIN_NAMES = tuple(domain["name"] for domain in DOMAINS)


def get_domain_definition(domain_name: str) -> dict | None:
    """Return one domain definition from the canonical interview catalog."""
    for domain in DOMAINS:
        if domain["name"] == domain_name:
            return domain
    return None


def get_first_question(domain_name: str) -> str | None:
    """Return the first canonical question for one interview domain."""
    domain = get_domain_definition(domain_name)
    if domain is None:
        return None
    questions = domain.get("questions", [])
    if not questions:
        return None
    return str(questions[0])


def question_belongs_to_domain(domain_name: str, question: str) -> bool:
    """Return True when the question is canonical for the given domain."""
    domain = get_domain_definition(domain_name)
    if domain is None:
        return False
    return question in domain.get("questions", [])
