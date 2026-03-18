"""
CNA Engine — Orchestrator Configuration
Model settings, retry parameters, and expert roster.
"""
from __future__ import annotations
from dataclasses import dataclass, field


# Default MLX model — Qwen3-8B-4bit gives ~150-180 tok/s on M4 Pro
MLX_DEFAULT_MODEL = "mlx-community/Qwen3-8B-4bit"


@dataclass
class OrchestratorConfig:
    """Configuration for the multi-agent orchestrator."""

    # LLM settings
    model: str = "gpt-oss:20b"
    ollama_url: str = "http://localhost:11434"
    temperature: float = 0.3
    max_tokens: int = 2048
    max_retries: int = 3
    timeout: int = 300  # seconds per LLM call

    # Expert roster (which domain experts to consult)
    experts: list[str] = field(
        default_factory=lambda: ["ground", "logistics", "air", "naval"]
    )

    # Backend selection
    backend: str = "ollama"              # "ollama" or "mlx"
    mlx_url: str = "http://localhost:8080"
    mlx_max_tokens: int = 6144           # mlx_lm defaults to 512; gpt-oss needs room for analysis+final channels

    # Situation-action engine (two-stage pipeline) — ALWAYS ON.
    # The expert+general pipeline (general.py/experts.py) is deprecated.
    use_situation_engine: bool = True

    # Tool-calling mode (agentic loop instead of single-shot JSON)
    tool_calling: bool = False
    tool_max_iterations: int = 8
    tool_timeout: int = 120  # seconds for entire tool-calling loop

    # Logging
    log_llm_calls: bool = True
