"""
core/services/ai_responder.py
BR4 – Generate an AI response suggestion using Ollama (free, local).
BR5 – Always stored as PENDING — never sent without human approval.

Setup:
    1. Install Ollama: https://ollama.com/download
    2. ollama pull llama3.2
    3. ollama serve
"""

import json
import logging
import urllib.request
import urllib.error
from django.conf import settings
from tradiePrototype.models import ClientRequest, AIResponseSuggestion

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a professional customer service assistant for a field service company. "
    "Draft a polite, warm, and concise response (3-5 sentences) to a client's job request. "
    "Acknowledge their request, be reassuring, and avoid promising specific prices or dates. "
    "Return ONLY the email body — no subject line, no greeting prefix, no extra commentary."
)

FALLBACK_RESPONSE = (
    "Thank you for reaching out to us. We have received your request and one of our "
    "team members will be in touch shortly to discuss your requirements. "
    "We appreciate your patience and look forward to assisting you."
)


def generate_ai_suggestion(client_request: ClientRequest) -> AIResponseSuggestion:
    """
    US4.1/4.2 – Generate an AI draft for a client request.
    US5.1/5.2 – Stored as PENDING; only sent after human approval.
    """
    prompt = (
        f"Client name: {client_request.contact_name}\n"
        f"Subject: {client_request.subject}\n"
        f"Message: {client_request.message}\n\n"
        "Please draft a professional response to this client request."
    )

    suggested_text = _call_ollama(prompt)

    suggestion = AIResponseSuggestion.objects.create(
        client_request=client_request,
        suggested_response=suggested_text,
        approval_status=AIResponseSuggestion.ApprovalStatus.PENDING,
    )

    logger.info("AIResponseSuggestion #%s created for ClientRequest #%s (PENDING)",
                suggestion.pk, client_request.pk)
    return suggestion


def _call_ollama(user_message: str) -> str:
    """Call the local Ollama server. Falls back to a placeholder if not running."""
    base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
    model    = getattr(settings, 'OLLAMA_MODEL',    'llama3.2')

    payload = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"].strip()

    except urllib.error.URLError as exc:
        logger.error("Ollama not reachable at %s — is 'ollama serve' running? Error: %s", base_url, exc)
        return FALLBACK_RESPONSE

    except Exception as exc:
        logger.error("Ollama call failed: %s", exc)
        return FALLBACK_RESPONSE