"""
CGAE Model Configurations

Maps available models to their provider, endpoint, and authentication settings.
Aligned with the CDCT framework's model roster.

Environment variables required:
  AZURE_API_KEY              - Azure API key (shared across Azure providers)
  AZURE_OPENAI_API_ENDPOINT  - Azure OpenAI endpoint (for gpt-5.4)
  FOUNDRY_MODELS_ENDPOINT    - Azure AI Foundry endpoint (for DeepSeek, Mistral, etc.)
  GEMMA_BASE_URL             - Modal endpoint for Gemma-4
  GEMMA_API_KEY              - API key for Gemma-4 (can be "not-needed")
  AWS_BEARER_TOKEN_BEDROCK   - AWS Bedrock bearer token (for nova-pro, claude, MiniMax)
"""

AVAILABLE_MODELS = [
    # --- Azure OpenAI ---
    {
        "model_name": "gpt-5.4",
        "deployment_name": "gpt-5.4",
        "provider": "azure_openai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "AZURE_OPENAI_API_ENDPOINT",
        "api_version": "2025-03-01-preview",
        "architecture": "reasoning-aligned",
        "family": "OpenAI",
        "tier_assignment": "contestant",
    },
    # --- Azure AI Foundry ---
    {
        "model_name": "DeepSeek-V3.2",
        "deployment_name": "DeepSeek-V3.2",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "mixture-of-experts",
        "family": "DeepSeek",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "Mistral-Large-3",
        "deployment_name": "Mistral-Large-3",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "dense",
        "family": "Mistral",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "grok-4-20-reasoning",
        "deployment_name": "grok-4-20-reasoning",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "dense",
        "family": "xAI",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "Phi-4",
        "deployment_name": "Phi-4",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "reasoning-aligned",
        "params": "14B",
        "family": "Microsoft",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "Llama-4-Maverick-17B-128E-Instruct-FP8",
        "deployment_name": "Llama-4-Maverick-17B-128E-Instruct-FP8",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "mixture-of-experts",
        "params": "17B (128 experts)",
        "family": "Meta",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "Kimi-K2.5",
        "deployment_name": "Kimi-K2.5",
        "provider": "azure_ai",
        "api_key_env_var": "AZURE_API_KEY",
        "endpoint_env_var": "FOUNDRY_MODELS_ENDPOINT",
        "architecture": "dense",
        "family": "Moonshot",
        "tier_assignment": "contestant",
    },
    # --- Gemma via Modal ---
    {
        "model_name": "gemma-4-27b-it",
        "deployment_name": "google/gemma-4-26B-A4B-it",
        "provider": "azure_ai",
        "api_key_env_var": "GEMMA_API_KEY",
        "endpoint_env_var": "GEMMA_BASE_URL",
        "architecture": "mixture-of-experts",
        "params": "27B (4B active)",
        "family": "Google",
        "tier_assignment": "contestant",
    },
    # --- AWS Bedrock ---
    {
        "model_name": "nova-pro",
        "model_id": "amazon.nova-pro-v1:0",
        "provider": "bedrock",
        "region": "us-east-1",
        "architecture": "dense",
        "family": "Amazon",
        "tier_assignment": "contestant",
    },
    {
        "model_name": "claude-sonnet-4.6",
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "provider": "bedrock",
        "region": "us-east-1",
        "architecture": "dense",
        "family": "Anthropic",
        "tier_assignment": "jury",
    },
    {
        "model_name": "MiniMax-M2.5",
        "model_id": "minimax.minimax-m2.5",
        "provider": "bedrock",
        "region": "us-east-1",
        "architecture": "dense",
        "family": "MiniMax",
        "tier_assignment": "contestant",
    },
]

# Models used as jury (for output verification)
JURY_MODELS = [m for m in AVAILABLE_MODELS if m["tier_assignment"] == "jury"]

# Models used as contestants (actual agents in the economy)
CONTESTANT_MODELS = [m for m in AVAILABLE_MODELS if m["tier_assignment"] != "jury"]


def get_model_config(model_name: str) -> dict:
    """Look up a model config by name."""
    for m in AVAILABLE_MODELS:
        if m["model_name"] == model_name:
            return m
    raise KeyError(f"Model '{model_name}' not found in AVAILABLE_MODELS")
