"""
CNA Engine — LLM Backend
Ollama and MLX REST API clients with retry logic.
Includes MockLLMClient for testing without a live LLM.
"""
from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from .config import OrchestratorConfig

logger = logging.getLogger(__name__)


def extract_json(text: str) -> dict:
    """
    Extract a JSON object from freeform LLM text.

    Tries in order:
    0. Strip gpt-oss channel markers (extract final channel content)
    1. Direct json.loads (clean JSON response)
    2. Strip ```json ... ``` code fences
    3. Find first balanced {...} block via brace counting
       (tries ALL balanced blocks, not just the first)
    4. Return {} fallback with warning
    """
    text = text.strip()

    # Step 0: strip gpt-oss <|channel|>analysis...<|channel|>final markers
    # The model emits thinking in an "analysis" channel, then the real answer
    # in a "final" channel. We only want the final channel content.
    channel_match = re.search(
        r'<\|channel\|>final<\|message\|>(.*?)(?:<\|end\|>|$)',
        text, re.DOTALL,
    )
    if channel_match:
        text = channel_match.group(1).strip()
        logger.debug("extract_json: stripped channel markers, inner len=%d", len(text))

    # Step 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 2: strip code fences
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Step 3: find balanced { ... } blocks — try all of them (largest first)
    candidates = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            in_string = False
            escape = False
            for j in range(i, len(text)):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == '\\' and in_string:
                    escape = True
                    continue
                if c == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i:j + 1])
                        i = j  # skip past this block
                        break
            else:
                # Unbalanced — skip this opening brace
                pass
        i += 1

    # Try candidates largest first (the real JSON object is usually the biggest)
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # Step 4: fallback
    logger.warning("extract_json: could not extract JSON from response (len=%d)", len(text))
    logger.debug("extract_json: raw content:\n%s", text[:3000])
    return {}


@dataclass
class LLMResponse:
    """Parsed response from the LLM."""
    content: str
    parsed: Optional[dict] = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


@dataclass
class ToolCallResult:
    """Result of a single tool call within an agentic loop."""
    tool_name: str
    tool_args: dict
    result: str  # JSON string returned to the LLM
    error: bool = False


@dataclass
class ToolCallingResponse:
    """Accumulated result of a multi-turn tool-calling loop."""
    tool_results: list = field(default_factory=list)  # list[ToolCallResult]
    final_content: str = ""
    stop_reason: str = "done"  # "done", "max_iterations", "timeout", "error"
    iterations: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    duration_ms: int = 0


class OllamaClient:
    """Client for the Ollama REST API (/api/chat)."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.base_url = config.ollama_url.rstrip("/")
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """
        Send a chat request to Ollama and return the parsed response.

        Args:
            messages: List of {role, content} message dicts.
            json_mode: If True, request structured JSON output.
            think: Control thinking/reasoning for models that support it.
                   None = default (False for qwen3, omitted for others).
                   False = disable thinking.
                   str like "budget_tokens:2048" = enable with budget.

        Returns:
            LLMResponse with parsed JSON dict (if json_mode) or raw content.

        Raises:
            LLMError: After exhausting all retries.
        """
        last_error = None
        call_start = time.monotonic()

        # Identify caller from messages for logging context
        caller_hint = ""
        for msg in messages:
            if msg.get("role") == "system":
                text = msg["content"][:120].replace("\n", " ")
                caller_hint = text
                break

        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = self._call(messages, json_mode, attempt, think=think)
                total_ms = int((time.monotonic() - call_start) * 1000)
                self.prompt_tokens_total += result.prompt_tokens
                self.completion_tokens_total += result.completion_tokens
                logger.info(
                    "LLM OK  model=%s  attempt=%d  duration=%dms  "
                    "prompt_tok=%d  completion_tok=%d  caller=[%s]",
                    result.model, attempt, result.duration_ms,
                    result.prompt_tokens, result.completion_tokens,
                    caller_hint[:80],
                )
                return result
            except (requests.RequestException, json.JSONDecodeError, LLMError) as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )
                # Empty content → same prompt will get same result, don't retry
                if isinstance(e, LLMError) and "empty content" in str(e).lower():
                    logger.warning(
                        "LLM empty content — skipping retries  caller=[%s]",
                        caller_hint[:80],
                    )
                    break
                if attempt < self.config.max_retries:
                    # Append retry hint for JSON extraction failures
                    is_json_fail = (
                        isinstance(e, json.JSONDecodeError)
                        or (isinstance(e, LLMError) and "json" in str(e).lower())
                    )
                    if is_json_fail and json_mode:
                        messages = messages + [{
                            "role": "user",
                            "content": "Your previous response was not valid JSON. "
                                       "Please respond with valid JSON only.",
                        }]

        total_ms = int((time.monotonic() - call_start) * 1000)
        raise LLMError(
            f"LLM call failed after {self.config.max_retries} attempts "
            f"({total_ms}ms total): {last_error}"
        )

    def _call(
        self,
        messages: list[dict],
        json_mode: bool,
        attempt: int,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Execute a single API call to Ollama."""
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            },
        }
        # Thinking/reasoning control for models that support it (e.g., Qwen3).
        # Ollama accepts True/False/"high"/"medium"/"low" only. The vLLM/MLX
        # "budget_tokens:N" form degrades to True here since Ollama has no
        # equivalent budget knob.
        if think is not None:
            if isinstance(think, str) and think.startswith("budget_tokens:"):
                payload["think"] = True
            else:
                payload["think"] = think
        elif "qwen3" in self.config.model.lower():
            payload["think"] = False

        start = time.monotonic()
        resp = requests.post(url, json=payload, timeout=self.config.timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            raise LLMError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        content = data.get("message", {}).get("content", "")

        if not content.strip():
            raise LLMError("Ollama returned empty content")

        parsed = None
        if json_mode:
            parsed = extract_json(content)
            if not parsed:
                raise LLMError("Could not extract JSON from response")

        return LLMResponse(
            content=content,
            parsed=parsed,
            model=data.get("model", self.config.model),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            duration_ms=duration_ms,
        )

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags", timeout=5
            )
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            return any(self.config.model in m for m in models)
        except requests.RequestException:
            return False


class MLXClient:
    """Client for mlx_lm.server (OpenAI-compatible /v1/chat/completions)."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.base_url = config.mlx_url.rstrip("/")
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """
        Send a chat request to mlx_lm.server and return the parsed response.

        Args:
            messages: List of {role, content} message dicts.
            json_mode: If True, extract JSON from response text.
            think: Control thinking/reasoning for models that support it.
                   Passed through for Qwen3 on MLX (budget_tokens support
                   via chat template). None = default, False = disable.

        Returns:
            LLMResponse with parsed JSON dict (if json_mode) or raw content.

        Raises:
            LLMError: After exhausting all retries.
        """
        last_error = None
        call_start = time.monotonic()

        caller_hint = ""
        for msg in messages:
            if msg.get("role") == "system":
                text = msg["content"][:120].replace("\n", " ")
                caller_hint = text
                break

        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = self._call(messages, json_mode, attempt, think=think)
                total_ms = int((time.monotonic() - call_start) * 1000)
                self.prompt_tokens_total += result.prompt_tokens
                self.completion_tokens_total += result.completion_tokens
                logger.info(
                    "LLM OK  model=%s  attempt=%d  duration=%dms  "
                    "prompt_tok=%d  completion_tok=%d  caller=[%s]",
                    result.model, attempt, result.duration_ms,
                    result.prompt_tokens, result.completion_tokens,
                    caller_hint[:80],
                )
                return result
            except (requests.RequestException, json.JSONDecodeError, LLMError) as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )
                if isinstance(e, LLMError) and "empty content" in str(e).lower():
                    logger.warning(
                        "LLM empty content — skipping retries  caller=[%s]",
                        caller_hint[:80],
                    )
                    break
                if attempt < self.config.max_retries:
                    is_json_fail = (
                        isinstance(e, json.JSONDecodeError)
                        or (isinstance(e, LLMError) and "json" in str(e).lower())
                    )
                    if is_json_fail and json_mode:
                        messages = messages + [{
                            "role": "user",
                            "content": "Your previous response was not valid JSON. "
                                       "Please respond with valid JSON only.",
                        }]

        total_ms = int((time.monotonic() - call_start) * 1000)
        raise LLMError(
            f"LLM call failed after {self.config.max_retries} attempts "
            f"({total_ms}ms total): {last_error}"
        )

    def _call(
        self,
        messages: list[dict],
        json_mode: bool,
        attempt: int,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Execute a single API call to mlx_lm.server."""
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.mlx_max_tokens,
        }

        # Thinking/reasoning control for Qwen3 on MLX.
        # mlx_lm.server passes chat_template_kwargs through to the template,
        # which Qwen3's template uses for enable_thinking / budget_tokens.
        if think is not None:
            if isinstance(think, str) and think.startswith("budget_tokens:"):
                try:
                    budget = int(think.split(":", 1)[1])
                    payload["chat_template_kwargs"] = {
                        "enable_thinking": True,
                        "budget_tokens": budget,
                    }
                except (ValueError, IndexError):
                    payload["chat_template_kwargs"] = {"enable_thinking": True}
            elif think is False:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            elif think is True or think:
                payload["chat_template_kwargs"] = {"enable_thinking": True}
        elif "qwen3" in self.config.model.lower():
            # Default: disable thinking for Qwen3 unless explicitly requested
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        start = time.monotonic()
        resp = requests.post(url, json=payload, timeout=self.config.timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            raise LLMError(
                f"MLX server returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        if not content.strip():
            raise LLMError("MLX server returned empty content")

        usage = data.get("usage", {})
        parsed = None
        if json_mode:
            parsed = extract_json(content)
            if not parsed:
                raise LLMError("Could not extract JSON from response")

        return LLMResponse(
            content=content,
            parsed=parsed,
            model=data.get("model", self.config.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
        )

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        max_iterations: int = 8,
        timeout: int = 120,
        on_tool_call: Optional[object] = None,
    ) -> ToolCallingResponse:
        """
        Agentic tool-calling loop over /v1/chat/completions.

        Sends messages + tool definitions to the server. When the model
        responds with tool_calls, executes the on_tool_call callback for
        each, appends tool results, and loops. Stops when the model
        returns content without tool_calls, or on max_iterations/timeout.

        Args:
            messages: Initial conversation (system + user).
            tools: OpenAI-format tool definitions.
            max_iterations: Hard cap on round-trips.
            timeout: Wall-clock seconds for the entire loop.
            on_tool_call: Callable(name, args) -> str. Returns JSON result.

        Returns:
            ToolCallingResponse with accumulated results.
        """
        url = f"{self.base_url}/v1/chat/completions"
        loop_start = time.monotonic()
        all_tool_results: list[ToolCallResult] = []
        total_prompt_tok = 0
        total_completion_tok = 0
        iteration = 0

        # Work on a mutable copy of messages
        msgs = list(messages)

        for iteration in range(1, max_iterations + 1):
            elapsed = time.monotonic() - loop_start
            if elapsed >= timeout:
                logger.warning(
                    "chat_with_tools: timeout after %.1fs (%d iterations)",
                    elapsed, iteration - 1,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    stop_reason="timeout",
                    iterations=iteration - 1,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=int(elapsed * 1000),
                )

            remaining = max(10, int(timeout - elapsed))

            payload: dict = {
                "model": self.config.model,
                "messages": msgs,
                "tools": tools,
                "temperature": self.config.temperature,
                "max_tokens": 2048,  # Tool iterations need far less than mlx_max_tokens
            }

            # Disable thinking for Qwen3 — without this, hidden reasoning
            # chains balloon request times from ~3s to 30-113s
            if "qwen3" in self.config.model.lower():
                payload["chat_template_kwargs"] = {"enable_thinking": False}

            # Cap per-request timeout so one slow request doesn't eat the
            # entire loop budget
            per_request_timeout = min(remaining, 60)

            try:
                resp = requests.post(url, json=payload, timeout=per_request_timeout)
            except requests.RequestException as e:
                logger.error("chat_with_tools: request failed iter=%d: %s", iteration, e)
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    stop_reason="error",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=int((time.monotonic() - loop_start) * 1000),
                )

            if resp.status_code != 200:
                logger.error(
                    "chat_with_tools: HTTP %d iter=%d: %s",
                    resp.status_code, iteration, resp.text[:500],
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    stop_reason="error",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=int((time.monotonic() - loop_start) * 1000),
                )

            data = resp.json()
            usage = data.get("usage", {})
            iter_prompt = usage.get("prompt_tokens", 0)
            iter_completion = usage.get("completion_tokens", 0)
            total_prompt_tok += iter_prompt
            total_completion_tok += iter_completion
            self.prompt_tokens_total += iter_prompt
            self.completion_tokens_total += iter_completion

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls")
            content = message.get("content", "") or ""

            # If no tool_calls, the model is done
            if not tool_calls:
                duration_ms = int((time.monotonic() - loop_start) * 1000)
                logger.info(
                    "chat_with_tools: done after %d iterations (%dms), "
                    "%d tool calls total  prompt_tok=%d  completion_tok=%d",
                    iteration, duration_ms, len(all_tool_results),
                    total_prompt_tok, total_completion_tok,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    final_content=content,
                    stop_reason="done",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=duration_ms,
                )

            # Append assistant message (content="" not None for mlx quirk)
            msgs.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": tool_calls,
            })

            # Execute each tool call
            for tc in tool_calls:
                fn = tc.get("function", {})
                tc_id = tc.get("id", f"call_{iteration}_{fn.get('name', 'unknown')}")
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")

                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                # Execute callback
                is_error = False
                try:
                    if on_tool_call:
                        result_str = on_tool_call(name, args)
                    else:
                        result_str = json.dumps({"error": "no handler registered"})
                        is_error = True
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})
                    is_error = True
                    logger.warning(
                        "chat_with_tools: tool %s raised: %s", name, e,
                    )

                all_tool_results.append(ToolCallResult(
                    tool_name=name,
                    tool_args=args,
                    result=result_str,
                    error=is_error,
                ))

                # Append tool response message
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_str,
                })

                logger.debug(
                    "chat_with_tools: iter=%d tool=%s args=%s error=%s",
                    iteration, name, args, is_error,
                )

        # Exhausted max_iterations
        duration_ms = int((time.monotonic() - loop_start) * 1000)
        logger.warning(
            "chat_with_tools: max_iterations=%d reached (%dms)  "
            "prompt_tok=%d  completion_tok=%d",
            max_iterations, duration_ms,
            total_prompt_tok, total_completion_tok,
        )
        return ToolCallingResponse(
            tool_results=all_tool_results,
            stop_reason="max_iterations",
            iterations=max_iterations,
            total_prompt_tokens=total_prompt_tok,
            total_completion_tokens=total_completion_tok,
            duration_ms=duration_ms,
        )

    def is_available(self) -> bool:
        """Check if mlx_lm.server is running."""
        try:
            resp = requests.get(
                f"{self.base_url}/v1/models", timeout=5
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False


class MockLLMClient:
    """
    Mock LLM client for testing. Returns canned JSON responses
    based on the system prompt role detected in messages.
    """

    def __init__(self, config: OrchestratorConfig = None):
        self.config = config or OrchestratorConfig()
        self.call_log: list[dict] = []
        self._custom_responses: dict[str, dict] = {}
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0

    def set_response(self, role_keyword: str, response: dict):
        """Register a custom response for a role keyword."""
        self._custom_responses[role_keyword] = response

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Return a canned response based on detected role."""
        self.call_log.append({"messages": messages, "json_mode": json_mode, "think": think})

        role = self._detect_role(messages)
        response_dict = self._get_response(role)

        content = json.dumps(response_dict)
        return LLMResponse(
            content=content,
            parsed=response_dict,
            model="mock",
            duration_ms=0,
        )

    def _detect_role(self, messages: list[dict]) -> str:
        """Detect which agent role is being queried from the system prompt."""
        system_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "").lower()
                break

        if "theater commander" in system_text or "general" in system_text:
            return "commander"
        elif "ground operations" in system_text:
            return "ground"
        elif "supply officer" in system_text or "logistics" in system_text:
            return "logistics"
        elif "air operations" in system_text:
            return "air"
        elif "naval" in system_text:
            return "naval"
        return "unknown"

    def _get_response(self, role: str) -> dict:
        """Get the canned response for a role."""
        # Check custom responses first
        if role in self._custom_responses:
            return self._custom_responses[role]

        # Default canned responses
        if role == "commander":
            return {
                "orders": [
                    {"command": "end_phase", "params": {}},
                ],
                "end_phase": True,
                "reasoning": "Mock commander: ending phase with no additional orders.",
            }

        # Expert recommendation format
        return {
            "role": role,
            "assessment": f"Mock {role} assessment: situation nominal.",
            "priority": "medium",
            "recommendations": [],
            "concerns": [],
        }

    def is_available(self) -> bool:
        return True


class LLMError(Exception):
    """Raised when LLM communication fails after retries."""
    pass
