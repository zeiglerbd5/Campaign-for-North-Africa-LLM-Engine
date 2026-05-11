"""
CNA Engine — LLM Backend
Ollama and MLX REST API clients with retry logic.
Includes MockLLMClient for testing without a live LLM.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from .config import OrchestratorConfig

logger = logging.getLogger(__name__)


ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-6"

# Bedrock default uses a US cross-region inference profile. Adjust for your
# region / account. Verify model access is enabled in the Bedrock console.
BEDROCK_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
BEDROCK_DEFAULT_REGION = "us-east-1"


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


class AnthropicClient:
    """
    Client for the Anthropic Messages API.

    Supports prompt caching on the system prompt and tool definitions, and
    extended thinking via budget_tokens for chat() (not chat_with_tools).

    Auth: reads ANTHROPIC_API_KEY from the environment. Fails fast if missing.
    """

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0
        # Track caching separately so we can see hit rate in logs.
        self.cache_read_tokens_total = 0
        self.cache_creation_tokens_total = 0
        self._client = None  # lazy: only instantiate when first used

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as e:
            raise LLMError(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from e
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY environment variable not set"
            )
        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=float(self.config.timeout),
        )
        return self._client

    @staticmethod
    def _split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """Pull the system message out and pass the rest through unchanged."""
        system_text = ""
        out = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                # Concatenate if multiple system messages (rare but possible).
                system_text = (system_text + "\n" + m.get("content", "")).strip()
            elif role in ("user", "assistant"):
                out.append({"role": role, "content": m.get("content", "")})
        return system_text, out

    @staticmethod
    def _build_system_param(system_text: str, cache: bool):
        if not system_text:
            return None
        if cache:
            return [{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }]
        return system_text

    @staticmethod
    def _convert_tools(tools: list[dict], cache: bool) -> list[dict]:
        """OpenAI function-calling format → Anthropic tool format."""
        out = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                f = t["function"]
                out.append({
                    "name": f["name"],
                    "description": f.get("description", ""),
                    "input_schema": f.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                })
            elif "name" in t and "input_schema" in t:
                # Already Anthropic-shaped — pass through, drop any cache_control
                # so we control where caching is applied.
                out.append({
                    k: v for k, v in t.items() if k != "cache_control"
                })
        if out and cache:
            # Anthropic caches everything up to and including the marked block.
            # Marking the last tool caches the entire tools array.
            out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
        return out

    @staticmethod
    def _build_thinking(think) -> Optional[dict]:
        """Map the engine's think param to Anthropic's thinking config."""
        if think is None or think is False:
            return None
        if isinstance(think, str) and think.startswith("budget_tokens:"):
            try:
                budget = int(think.split(":", 1)[1])
                # Anthropic minimum is 1024.
                return {"type": "enabled", "budget_tokens": max(budget, 1024)}
            except (ValueError, IndexError):
                return None
        if think is True:
            return {"type": "enabled", "budget_tokens": 2048}
        return None

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Single-shot Messages API call with retries."""
        last_error = None
        call_start = time.monotonic()

        caller_hint = ""
        for msg in messages:
            if msg.get("role") == "system":
                caller_hint = msg.get("content", "")[:120].replace("\n", " ")
                break

        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = self._call_single(messages, json_mode, think)
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
            except (json.JSONDecodeError, LLMError) as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )
                if isinstance(e, LLMError) and "empty content" in str(e).lower():
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
            except Exception as e:
                # SDK-level errors (rate limit, network, etc.) — retry with same prompt
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )

        total_ms = int((time.monotonic() - call_start) * 1000)
        raise LLMError(
            f"Anthropic call failed after {self.config.max_retries} attempts "
            f"({total_ms}ms total): {last_error}"
        )

    def _call_single(
        self,
        messages: list[dict],
        json_mode: bool,
        think: Optional[object],
    ) -> LLMResponse:
        client = self._get_client()
        system_text, msgs = self._split_messages(messages)
        system = self._build_system_param(
            system_text, self.config.anthropic_cache
        )
        thinking = self._build_thinking(think)

        kwargs: dict = {
            "model": self.config.model,
            "max_tokens": self.config.anthropic_max_tokens,
            "messages": msgs,
            "temperature": self.config.temperature,
        }
        if system is not None:
            kwargs["system"] = system
        if thinking is not None:
            kwargs["thinking"] = thinking
            # Extended thinking requires temperature=1.
            kwargs["temperature"] = 1.0
            # Ensure max_tokens leaves room beyond the thinking budget.
            min_max = thinking["budget_tokens"] + 1024
            if kwargs["max_tokens"] < min_max:
                kwargs["max_tokens"] = min_max

        start = time.monotonic()
        response = client.messages.create(**kwargs)
        duration_ms = int((time.monotonic() - start) * 1000)

        content_text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not content_text.strip():
            raise LLMError("Anthropic returned empty content")

        parsed = None
        if json_mode:
            parsed = extract_json(content_text)
            if not parsed:
                raise LLMError("Could not extract JSON from response")

        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens_total += cache_read
        self.cache_creation_tokens_total += cache_create

        return LLMResponse(
            content=content_text,
            parsed=parsed,
            model=response.model,
            prompt_tokens=usage.input_tokens + cache_read + cache_create,
            completion_tokens=usage.output_tokens,
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
        """Agentic tool-calling loop using Anthropic's tool_use blocks."""
        client = self._get_client()
        loop_start = time.monotonic()

        system_text, anth_msgs = self._split_messages(messages)
        system = self._build_system_param(
            system_text, self.config.anthropic_cache
        )
        anth_tools = self._convert_tools(tools, self.config.anthropic_cache)

        all_tool_results: list[ToolCallResult] = []
        total_prompt_tok = 0
        total_completion_tok = 0
        iteration = 0
        msgs = list(anth_msgs)
        final_content = ""

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

            kwargs: dict = {
                "model": self.config.model,
                "max_tokens": min(self.config.anthropic_max_tokens, 4096),
                "messages": msgs,
                "tools": anth_tools,
                "temperature": self.config.temperature,
            }
            if system is not None:
                kwargs["system"] = system

            try:
                response = client.messages.create(**kwargs)
            except Exception as e:
                logger.error(
                    "chat_with_tools: request failed iter=%d: %s",
                    iteration, e,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    stop_reason="error",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=int((time.monotonic() - loop_start) * 1000),
                )

            usage = response.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
            iter_prompt = usage.input_tokens + cache_read + cache_create
            iter_completion = usage.output_tokens
            total_prompt_tok += iter_prompt
            total_completion_tok += iter_completion
            self.prompt_tokens_total += iter_prompt
            self.completion_tokens_total += iter_completion
            self.cache_read_tokens_total += cache_read
            self.cache_creation_tokens_total += cache_create

            # Reassemble the assistant message with its original content blocks.
            # Anthropic requires echoing tool_use blocks back verbatim.
            assistant_content = []
            text_parts = []
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    assistant_content.append(
                        {"type": "text", "text": block.text}
                    )
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            if response.stop_reason == "end_turn" or not tool_uses:
                duration_ms = int((time.monotonic() - loop_start) * 1000)
                final_content = "".join(text_parts)
                logger.info(
                    "chat_with_tools: done after %d iterations (%dms), "
                    "%d tool calls total  prompt_tok=%d  completion_tok=%d  "
                    "cache_read=%d",
                    iteration, duration_ms, len(all_tool_results),
                    total_prompt_tok, total_completion_tok, cache_read,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    final_content=final_content,
                    stop_reason="done",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=duration_ms,
                )

            msgs.append({"role": "assistant", "content": assistant_content})

            tool_result_blocks = []
            for tu in tool_uses:
                name = tu.name
                args = tu.input or {}
                is_error = False
                try:
                    if on_tool_call:
                        result_str = on_tool_call(name, args)
                    else:
                        result_str = json.dumps(
                            {"error": "no handler registered"}
                        )
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

                block = {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_str,
                }
                if is_error:
                    block["is_error"] = True
                tool_result_blocks.append(block)

                logger.debug(
                    "chat_with_tools: iter=%d tool=%s args=%s error=%s",
                    iteration, name, args, is_error,
                )

            msgs.append({"role": "user", "content": tool_result_blocks})

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
        """True if the SDK is importable and the API key is set."""
        try:
            self._get_client()
            return True
        except LLMError:
            return False


class BedrockClient:
    """
    Client for AWS Bedrock via the Converse API.

    Uses boto3 with IAM credentials from the standard chain (env vars,
    ~/.aws/credentials, or the Fargate task role at runtime). Same shape
    as AnthropicClient — implements chat() and chat_with_tools() — but
    routes through bedrock-runtime.converse().

    Caching: insert cachePoint blocks after system text and after the
    tools list. Requires model access to be enabled for the configured
    modelId in the AWS Bedrock console.
    """

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0
        self.cache_read_tokens_total = 0
        self.cache_creation_tokens_total = 0
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as e:
            raise LLMError(
                "boto3 not installed. Run: pip install boto3"
            ) from e
        try:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.config.bedrock_region,
            )
        except Exception as e:
            raise LLMError(f"Failed to create bedrock-runtime client: {e}") from e
        return self._client

    @staticmethod
    def _split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """Pull the system message out and convert the rest to Converse format."""
        system_text = ""
        out = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_text = (system_text + "\n" + content).strip()
            elif role in ("user", "assistant"):
                # Converse content is a list of blocks.
                out.append({
                    "role": role,
                    "content": [{"text": content}] if isinstance(content, str) else content,
                })
        return system_text, out

    @staticmethod
    def _build_system_blocks(system_text: str, cache: bool) -> list[dict]:
        if not system_text:
            return []
        blocks = [{"text": system_text}]
        if cache:
            blocks.append({"cachePoint": {"type": "default"}})
        return blocks

    @staticmethod
    def _convert_tools(tools: list[dict], cache: bool) -> Optional[dict]:
        """OpenAI function-calling format → Bedrock Converse toolConfig."""
        if not tools:
            return None
        specs = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                f = t["function"]
                specs.append({
                    "toolSpec": {
                        "name": f["name"],
                        "description": f.get("description", ""),
                        "inputSchema": {
                            "json": f.get(
                                "parameters",
                                {"type": "object", "properties": {}},
                            ),
                        },
                    },
                })
        tool_list: list = list(specs)
        if tool_list and cache:
            tool_list.append({"cachePoint": {"type": "default"}})
        return {"tools": tool_list}

    @staticmethod
    def _build_additional_fields(think) -> Optional[dict]:
        """Map the engine's think param to Bedrock's additionalModelRequestFields."""
        if think is None or think is False:
            return None
        if isinstance(think, str) and think.startswith("budget_tokens:"):
            try:
                budget = int(think.split(":", 1)[1])
                return {
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": max(budget, 1024),
                    },
                }
            except (ValueError, IndexError):
                return None
        if think is True:
            return {"thinking": {"type": "enabled", "budget_tokens": 2048}}
        return None

    def _accumulate_usage(self, usage: dict) -> tuple[int, int, int, int]:
        in_tok = usage.get("inputTokens", 0) or 0
        out_tok = usage.get("outputTokens", 0) or 0
        cache_read = usage.get("cacheReadInputTokens", 0) or 0
        cache_create = usage.get("cacheWriteInputTokens", 0) or 0
        self.prompt_tokens_total += in_tok + cache_read + cache_create
        self.completion_tokens_total += out_tok
        self.cache_read_tokens_total += cache_read
        self.cache_creation_tokens_total += cache_create
        return in_tok, out_tok, cache_read, cache_create

    def chat(
        self,
        messages: list[dict],
        json_mode: bool = True,
        think: Optional[object] = None,
    ) -> LLMResponse:
        """Single-shot Converse API call with retries."""
        last_error = None
        call_start = time.monotonic()

        caller_hint = ""
        for msg in messages:
            if msg.get("role") == "system":
                caller_hint = msg.get("content", "")[:120].replace("\n", " ")
                break

        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = self._call_single(messages, json_mode, think)
                logger.info(
                    "LLM OK  model=%s  attempt=%d  duration=%dms  "
                    "prompt_tok=%d  completion_tok=%d  caller=[%s]",
                    result.model, attempt, result.duration_ms,
                    result.prompt_tokens, result.completion_tokens,
                    caller_hint[:80],
                )
                return result
            except (json.JSONDecodeError, LLMError) as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )
                if isinstance(e, LLMError) and "empty content" in str(e).lower():
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
            except Exception as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                logger.warning(
                    "LLM FAIL  attempt=%d/%d  elapsed=%dms  error=%s  caller=[%s]",
                    attempt, self.config.max_retries, elapsed_ms, e,
                    caller_hint[:80],
                )

        total_ms = int((time.monotonic() - call_start) * 1000)
        raise LLMError(
            f"Bedrock call failed after {self.config.max_retries} attempts "
            f"({total_ms}ms total): {last_error}"
        )

    def _call_single(
        self,
        messages: list[dict],
        json_mode: bool,
        think: Optional[object],
    ) -> LLMResponse:
        client = self._get_client()
        system_text, msgs = self._split_messages(messages)
        system_blocks = self._build_system_blocks(
            system_text, self.config.anthropic_cache
        )
        additional = self._build_additional_fields(think)

        kwargs: dict = {
            "modelId": self.config.model,
            "messages": msgs,
            "inferenceConfig": {
                "maxTokens": self.config.anthropic_max_tokens,
                "temperature": self.config.temperature,
            },
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if additional is not None:
            kwargs["additionalModelRequestFields"] = additional
            # Extended thinking requires temperature=1.
            kwargs["inferenceConfig"]["temperature"] = 1.0
            min_max = additional["thinking"]["budget_tokens"] + 1024
            if kwargs["inferenceConfig"]["maxTokens"] < min_max:
                kwargs["inferenceConfig"]["maxTokens"] = min_max

        start = time.monotonic()
        response = client.converse(**kwargs)
        duration_ms = int((time.monotonic() - start) * 1000)

        msg = response.get("output", {}).get("message", {})
        content_text = "".join(
            b["text"] for b in msg.get("content", []) if "text" in b
        )
        if not content_text.strip():
            raise LLMError("Bedrock returned empty content")

        parsed = None
        if json_mode:
            parsed = extract_json(content_text)
            if not parsed:
                raise LLMError("Could not extract JSON from response")

        usage = response.get("usage", {})
        in_tok, out_tok, cache_read, cache_create = self._accumulate_usage(usage)

        return LLMResponse(
            content=content_text,
            parsed=parsed,
            model=self.config.model,
            prompt_tokens=in_tok + cache_read + cache_create,
            completion_tokens=out_tok,
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
        """Agentic tool-calling loop using Bedrock Converse toolUse blocks."""
        client = self._get_client()
        loop_start = time.monotonic()

        system_text, bedrock_msgs = self._split_messages(messages)
        system_blocks = self._build_system_blocks(
            system_text, self.config.anthropic_cache
        )
        tool_config = self._convert_tools(tools, self.config.anthropic_cache)

        all_tool_results: list[ToolCallResult] = []
        total_prompt_tok = 0
        total_completion_tok = 0
        iteration = 0
        msgs = list(bedrock_msgs)
        final_content = ""

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

            kwargs: dict = {
                "modelId": self.config.model,
                "messages": msgs,
                "inferenceConfig": {
                    "maxTokens": min(self.config.anthropic_max_tokens, 4096),
                    "temperature": self.config.temperature,
                },
            }
            if system_blocks:
                kwargs["system"] = system_blocks
            if tool_config:
                kwargs["toolConfig"] = tool_config

            try:
                response = client.converse(**kwargs)
            except Exception as e:
                logger.error(
                    "chat_with_tools: request failed iter=%d: %s",
                    iteration, e,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    stop_reason="error",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=int((time.monotonic() - loop_start) * 1000),
                )

            usage = response.get("usage", {})
            in_tok, out_tok, cache_read, _ = self._accumulate_usage(usage)
            iter_prompt = in_tok + cache_read + (usage.get("cacheWriteInputTokens", 0) or 0)
            iter_completion = out_tok
            total_prompt_tok += iter_prompt
            total_completion_tok += iter_completion

            msg = response.get("output", {}).get("message", {})
            stop_reason = response.get("stopReason")

            # Reassemble the assistant message — echo content blocks verbatim.
            assistant_content = []
            text_parts = []
            tool_uses = []
            for block in msg.get("content", []):
                if "text" in block:
                    text_parts.append(block["text"])
                    assistant_content.append({"text": block["text"]})
                elif "toolUse" in block:
                    tu = block["toolUse"]
                    tool_uses.append(tu)
                    assistant_content.append({"toolUse": tu})

            if stop_reason == "end_turn" or not tool_uses:
                duration_ms = int((time.monotonic() - loop_start) * 1000)
                final_content = "".join(text_parts)
                logger.info(
                    "chat_with_tools: done after %d iterations (%dms), "
                    "%d tool calls total  prompt_tok=%d  completion_tok=%d  "
                    "cache_read=%d",
                    iteration, duration_ms, len(all_tool_results),
                    total_prompt_tok, total_completion_tok, cache_read,
                )
                return ToolCallingResponse(
                    tool_results=all_tool_results,
                    final_content=final_content,
                    stop_reason="done",
                    iterations=iteration,
                    total_prompt_tokens=total_prompt_tok,
                    total_completion_tokens=total_completion_tok,
                    duration_ms=duration_ms,
                )

            msgs.append({"role": "assistant", "content": assistant_content})

            tool_result_blocks = []
            for tu in tool_uses:
                name = tu.get("name", "")
                tool_use_id = tu.get("toolUseId", "")
                args = tu.get("input", {}) or {}
                is_error = False
                try:
                    if on_tool_call:
                        result_str = on_tool_call(name, args)
                    else:
                        result_str = json.dumps(
                            {"error": "no handler registered"}
                        )
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

                tr_block = {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_str}],
                        "status": "error" if is_error else "success",
                    },
                }
                tool_result_blocks.append(tr_block)

                logger.debug(
                    "chat_with_tools: iter=%d tool=%s args=%s error=%s",
                    iteration, name, args, is_error,
                )

            msgs.append({"role": "user", "content": tool_result_blocks})

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
        """True if boto3 imports and a bedrock-runtime client can be created."""
        try:
            self._get_client()
            return True
        except LLMError:
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
