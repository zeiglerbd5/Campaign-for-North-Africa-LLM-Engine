"""
CNA Engine — Multi-Agent Orchestrator
LLM-powered multi-agent system for playing Campaign for North Africa.
"""
from .config import OrchestratorConfig
from .llm_backend import (
    OllamaClient, MockLLMClient, MLXClient, AnthropicClient, BedrockClient,
)
from .mock_strategies import SmartMockLLMClient
from .memory import TurnMemory
from .experts import ExpertAgent, ExpertRecommendation
from .general import GeneralAgent
from .orchestrator import GameOrchestrator

__all__ = [
    "OrchestratorConfig",
    "OllamaClient",
    "MockLLMClient",
    "MLXClient",
    "AnthropicClient",
    "BedrockClient",
    "SmartMockLLMClient",
    "TurnMemory",
    "ExpertAgent",
    "ExpertRecommendation",
    "GeneralAgent",
    "GameOrchestrator",
]
