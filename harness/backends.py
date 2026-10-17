"""One model call: instructions + request in, one JSON object (matching a schema) out.

    claude-cli  `claude -p` on your Claude subscription. Each call starts a process (a few seconds of overhead).
                ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are removed from its environment, so it can't bill an
                API account by mistake.
    api         The Claude API with structured outputs (output_config.format). The instructions are sent as a
                cached system block, so repeated calls with the same worker read them from the cache.
    fake        For tests: set FAKE to a function (request dict) -> answer dict.

Every call takes a timeout (seconds); the caller passes what's left of its lease.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

# Models that support the server-side refusal fallback ("default" routes by refusal category).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

FAKE = None


class BackendError(Exception):
    """The call failed: timeout, refusal, bad output, or an API error. The job can be retried."""


class UsageLimitError(BackendError):
    """The subscription's usage limit (or the API's rate limit) was reached: stop calling for a while."""

    def __init__(self, message, pause_s=None):
        super().__init__(message)
        self.pause_s = pause_s


def call(backend, *, system, prompt, schema, model, effort, max_tokens, timeout):
    """Return (answer dict, info dict with "model" and "ms")."""
    if timeout <= 0.5:
        raise BackendError("no time left for a model call")
    start = time.perf_counter()
    if backend == "fake":
        if FAKE is None:
            raise BackendError("no fake backend set")
        answer = FAKE({"system": system, "prompt": prompt, "schema": schema, "model": model, "timeout": timeout})
        used = model
    elif backend == "api":
        answer, used = _api(system, prompt, schema, model, effort, max_tokens, timeout)
    elif backend == "claude-cli":
        answer, used = _cli(system, prompt, schema, model, effort, timeout)
    else:
        raise BackendError(f"unknown backend {backend!r}")
    if not isinstance(answer, dict):
        raise BackendError("the answer is not a JSON object")
    return answer, {"model": used, "ms": round((time.perf_counter() - start) * 1000)}


_client = None


def _api(system, prompt, schema, model, effort, max_tokens, timeout):
    global _client
    import anthropic  # only needed for this backend

    if _client is None:
        _client = anthropic.Anthropic()
    request = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    client = _client.with_options(timeout=timeout, max_retries=0)  # the lease is short: no silent retries
    try:
        if model in FALLBACK_MODELS:
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
        else:
            response = client.messages.create(**request)
    except anthropic.RateLimitError as err:
        retry_after = err.response.headers.get("retry-after")
        raise UsageLimitError("API rate limit", pause_s=float(retry_after) if retry_after else 30) from err
    except anthropic.APITimeoutError as err:
        raise BackendError(f"API timeout after {timeout:.0f} s") from err
    except anthropic.APIStatusError as err:
        raise BackendError(f"API error {err.status_code}: {err.message}") from err
    except anthropic.APIConnectionError as err:
        raise BackendError("API connection error") from err

    if response.stop_reason == "refusal":
        raise BackendError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise BackendError("the answer was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text), response.model
    except ValueError as err:
        raise BackendError("the answer is not valid JSON") from err


def _cli(system, prompt, schema, model, effort, timeout):
    claude = shutil.which("claude")
    if not claude:
        raise BackendError("the claude CLI isn't on PATH; install Claude Code or use the api backend")
    cmd = [
        claude, "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--system-prompt", system,
        "--tools", "",               # no file, shell or web tools: it can only answer
        "--strict-mcp-config",       # and no MCP servers from your settings
        "--no-session-persistence",  # keep these calls out of your session history
        "--effort", effort,
        "--model", model,
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        # Run outside the project so no CLAUDE.md or project settings are picked up.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir(), env=env)
    except subprocess.TimeoutExpired as err:  # subprocess.run kills the process
        raise BackendError(f"claude CLI timeout after {timeout:.0f} s") from err
    try:
        out = json.loads(proc.stdout)
    except ValueError as err:
        message = (proc.stderr or proc.stdout).strip()[:300]
        if _is_usage_limit(message):
            raise UsageLimitError(message) from err
        raise BackendError(f"claude CLI failed: {message}") from err
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        message = str(out.get("result"))[:300]
        if _is_usage_limit(message):
            raise UsageLimitError(message)
        raise BackendError(f"claude CLI error: {message}")
    used = ", ".join(out.get("modelUsage", {})) or model
    return out["structured_output"], used


def _is_usage_limit(message):
    text = message.lower()
    return "limit" in text and any(w in text for w in ("usage", "reached", "rate", "quota"))
