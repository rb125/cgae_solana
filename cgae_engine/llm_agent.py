"""
LLM-backed Agent - Calls real Azure AI Foundry model endpoints.

Reuses the proven agent infrastructure from the DDFT/EECT frameworks
(AzureOpenAIAgent, AzureAIAgent) but wrapped for the CGAE economy loop.

Each LLMAgent:
- Has a real model backing it (e.g., gpt-5, deepseek-v3.1, phi-4)
- Executes tasks by sending prompts to the model and receiving outputs
- Has its robustness measured by actual CDCT/DDFT/EECT audits (or synthetics until wired)
- Competes in the CGAE economy alongside other LLM-backed agents
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from openai import AzureOpenAI, OpenAI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry handler (inline to avoid import path issues with framework code)
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 2.0
    max_delay: float = 60.0


def call_with_retry(api_call, config: RetryConfig, log_prefix: str = ""):
    retries = 0
    while True:
        try:
            return api_call()
        except Exception as e:
            retries += 1
            if retries > config.max_retries:
                logger.error(f"{log_prefix} Final attempt failed: {e}")
                raise
            delay = min(config.max_delay, config.base_delay * (2 ** (retries - 1)))
            logger.warning(
                f"{log_prefix} Attempt {retries}/{config.max_retries} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Client pools (thread-safe singletons)
# ---------------------------------------------------------------------------

_azure_openai_clients: dict[str, AzureOpenAI] = {}
_azure_openai_lock = Lock()

_openai_clients: dict[str, OpenAI] = {}
_openai_lock = Lock()


def _get_azure_openai_client(api_key: str, endpoint: str, api_version: str) -> AzureOpenAI:
    key = f"{endpoint}:{api_version}"
    if key not in _azure_openai_clients:
        with _azure_openai_lock:
            if key not in _azure_openai_clients:
                _azure_openai_clients[key] = AzureOpenAI(
                    api_key=api_key,
                    azure_endpoint=endpoint,
                    api_version=api_version,
                )
    return _azure_openai_clients[key]


def _get_openai_client(base_url: str, api_key: str) -> OpenAI:
    key = f"{base_url}"
    if key not in _openai_clients:
        with _openai_lock:
            if key not in _openai_clients:
                _openai_clients[key] = OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                )
    return _openai_clients[key]


# ---------------------------------------------------------------------------
# LLM Agent
# ---------------------------------------------------------------------------

class LLMAgent:
    """
    A live LLM agent backed by an Azure AI Foundry model endpoint.

    Provides:
    - chat(messages) -> str: Send messages, get response
    - execute_task(prompt, system_prompt) -> str: Execute a task
    - Token/call tracking for cost accounting
    """

    def __init__(self, model_config: dict):
        self.model_name: str = model_config["model_name"]
        self.deployment_name: str = model_config.get("deployment_name", model_config.get("model_id", ""))
        self.provider: str = model_config["provider"]
        self.family: str = model_config.get("family", "Unknown")
        self.retry_config = RetryConfig()

        # Tracking
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_errors: int = 0
        self.total_latency_ms: float = 0.0

        if self.provider == "bedrock":
            # Bedrock uses Converse API with bearer token auth
            self._model_id = model_config["model_id"]
            region = model_config.get("region", "us-east-1")
            self._bedrock_url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{self._model_id}/converse"
            self._bedrock_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
            if not self._bedrock_key:
                raise EnvironmentError(f"Missing env var AWS_BEARER_TOKEN_BEDROCK for model {self.model_name}")
            self._client = None
        else:
            # Azure OpenAI / Azure AI Foundry
            api_key_var = model_config["api_key_env_var"]
            endpoint_var = model_config["endpoint_env_var"]
            self._api_key = os.environ.get(api_key_var, "")
            self._endpoint = os.environ.get(endpoint_var, "")
            self._api_version = model_config.get("api_version", "2025-03-01-preview")

            if not self._api_key:
                raise EnvironmentError(f"Missing env var {api_key_var} for model {self.model_name}")
            if not self._endpoint:
                raise EnvironmentError(f"Missing env var {endpoint_var} for model {self.model_name}")

            if self.provider == "azure_openai":
                self._client = _get_azure_openai_client(
                    self._api_key, self._endpoint, self._api_version
                )
            elif self.provider == "azure_ai":
                self._client = _get_openai_client(self._endpoint, self._api_key)
            else:
                raise ValueError(f"Unsupported provider: {self.provider}")

    def chat(self, messages: list[dict]) -> str:
        """
        Send messages to the model and return the response text.
        Tracks tokens and latency for cost accounting.
        """
        log_prefix = f"[{self.model_name}]"

        if self.provider == "bedrock":
            return self._chat_bedrock(messages, log_prefix)

        def _call():
            kwargs = {
                "model": self.deployment_name,
                "messages": messages,
                "timeout": 180,
            }
            # Azure OpenAI supports max_completion_tokens; AI Foundry uses temperature
            if self.provider == "azure_openai":
                kwargs["max_completion_tokens"] = 8192
            else:
                kwargs["temperature"] = 0.0
                kwargs["max_tokens"] = 4096

            start = time.time()
            response = self._client.chat.completions.create(**kwargs)
            latency = (time.time() - start) * 1000

            # Track usage
            self.total_calls += 1
            self.total_latency_ms += latency
            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens or 0
                self.total_output_tokens += response.usage.completion_tokens or 0

            return response.choices[0].message.content

        try:
            return call_with_retry(_call, self.retry_config, log_prefix)
        except Exception as e:
            self.total_errors += 1
            raise

    def _chat_bedrock(self, messages: list[dict], log_prefix: str) -> str:
        """Call AWS Bedrock Converse API with bearer token auth."""
        import urllib.request
        import urllib.error

        def _call():
            bedrock_msgs = [
                {"role": m["role"], "content": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"
            ]
            system_parts = [
                {"text": m["content"]} for m in messages if m["role"] == "system"
            ]
            body = {
                "messages": bedrock_msgs,
                "inferenceConfig": {"temperature": 0.0, "maxTokens": 4096},
            }
            if system_parts:
                body["system"] = system_parts

            data = json.dumps(body).encode()
            req = urllib.request.Request(
                self._bedrock_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._bedrock_key}",
                },
            )
            start = time.time()
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read())
            latency = (time.time() - start) * 1000

            self.total_calls += 1
            self.total_latency_ms += latency
            usage = result.get("usage", {})
            self.total_input_tokens += usage.get("inputTokens", 0)
            self.total_output_tokens += usage.get("outputTokens", 0)

            content = result["output"]["message"]["content"]
            for block in content:
                if "text" in block:
                    return block["text"]
            return str(content)

        try:
            return call_with_retry(_call, self.retry_config, log_prefix)
        except Exception:
            self.total_errors += 1
            raise

    def execute_task(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Execute a task with an optional system prompt."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages)

    def usage_summary(self) -> dict:
        """Return usage stats for cost accounting."""
        return {
            "model": self.model_name,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_errors": self.total_errors,
            "avg_latency_ms": (
                self.total_latency_ms / self.total_calls
                if self.total_calls > 0 else 0
            ),
        }

    def __repr__(self):
        return f"LLMAgent({self.model_name}, provider={self.provider})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_llm_agent(model_config: dict) -> LLMAgent:
    """Create an LLM agent from a model config dict."""
    return LLMAgent(model_config)


def create_llm_agents(model_configs: list[dict]) -> dict[str, LLMAgent]:
    """Create all LLM agents from a list of configs. Returns {model_name: agent}."""
    agents = {}
    for config in model_configs:
        try:
            agent = create_llm_agent(config)
            agents[agent.model_name] = agent
            logger.info(f"Created LLM agent: {agent.model_name} ({agent.provider})")
        except EnvironmentError as e:
            logger.warning(f"Skipping {config['model_name']}: {e}")
    return agents
