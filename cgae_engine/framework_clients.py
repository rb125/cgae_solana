"""
HTTP API clients for the three CGAE diagnostic frameworks.

Each framework is hosted as an independent service and exposes a REST API.
Configure their base URLs via environment variables:

  CDCT_API_URL  — default http://localhost:8001
  DDFT_API_URL  — default http://localhost:8002
  EECT_API_URL  — default http://localhost:8003

API contracts
─────────────
CDCT
  POST /run_experiment
        req : {model_name, model_config, api_keys, concept,
               prompt_strategy, evaluation_mode}
        resp: {cc, results}
  GET  /score/{model_name}
        resp: {cc, found}

DDFT
  POST /assess
        req : {model_name, model_config, api_keys,
               concepts, compression_levels}
        resp: {er, ih, ci_score, phenotype}
  GET  /score/{model_name}
        resp: {er, ih, found}

EECT
  POST /dialogue
        req : {model_name, model_config, api_keys,
               dilemma, compression_level}
        resp: {turns}
  GET  /score/{model_name}
        resp: {as_, found}
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

import re

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300  # seconds — framework runs can be slow


class FrameworkAPIError(RuntimeError):
    """Raised when a framework API call fails."""


def _redact(text: str) -> str:
    """Strip anything that looks like an API key or secret from error text."""
    return re.sub(r'[A-Za-z0-9+/=]{20,}', '<REDACTED>', text)


def _post(url: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """POST JSON payload and return parsed response.  Raises FrameworkAPIError on failure."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        raise FrameworkAPIError(f"Cannot connect to {url}") from exc
    except requests.exceptions.Timeout as exc:
        raise FrameworkAPIError(f"Timeout calling {url}") from exc
    except requests.exceptions.HTTPError as exc:
        raise FrameworkAPIError(
            f"HTTP {exc.response.status_code} from {url}: {_redact(exc.response.text[:400])}"
        ) from exc
    except Exception as exc:
        raise FrameworkAPIError(f"Unexpected error calling {url}: {_redact(str(exc))}") from exc


def _get(url: str, timeout: int = 30) -> dict:
    """GET request returning parsed JSON.  Returns {} if 404."""
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        raise FrameworkAPIError(f"Cannot connect to {url}") from exc
    except requests.exceptions.Timeout as exc:
        raise FrameworkAPIError(f"Timeout calling {url}") from exc
    except requests.exceptions.HTTPError as exc:
        raise FrameworkAPIError(
            f"HTTP {exc.response.status_code} from {url}: {_redact(exc.response.text[:400])}"
        ) from exc
    except Exception as exc:
        raise FrameworkAPIError(f"Unexpected error calling {url}: {_redact(str(exc))}") from exc


# ---------------------------------------------------------------------------
# CDCT client
# ---------------------------------------------------------------------------

class CDCTClient:
    """
    Client for the CDCT (Compression-Decay Comprehension Test) API service.

    The CDCT service tests Constraint Compliance (CC) by measuring
    instruction-following under input compression.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or os.getenv("CDCT_API_URL", "http://localhost:8001")).rstrip("/")

    def run_experiment(
        self,
        model_name: str,
        model_config: dict,
        api_keys: dict,
        concept: str = "logic_modus_ponens",
        prompt_strategy: str = "compression_aware",
        evaluation_mode: str = "balanced",
    ) -> dict:
        """
        Run a CDCT experiment against a live model.

        Returns a dict with at least:
          cc      — Constraint Compliance score in [0, 1]
          results — Raw framework result object
        """
        url = f"{self.base_url}/run_experiment"
        payload = {
            "model": model_name,
            "model_name": model_name,
            "model_config": model_config,
            "api_keys": api_keys,
            "concept": concept,
            "prompt_strategy": prompt_strategy,
            "evaluation_mode": evaluation_mode,
        }
        logger.debug(f"[CDCT] POST {url} model={model_name}")
        return _post(url, payload)

    def get_score(self, model_name: str) -> dict:
        """
        Retrieve a pre-computed CC score for *model_name*.

        Returns a dict with:
          cc    — pre-computed score (float)
          found — True if a stored result exists for this model
        """
        url = f"{self.base_url}/score/{model_name}"
        logger.debug(f"[CDCT] GET {url}")
        return _get(url)


# ---------------------------------------------------------------------------
# DDFT client
# ---------------------------------------------------------------------------

class DDFTClient:
    """
    Client for the DDFT (Drill-Down Fabrication Test) API service.

    The DDFT service tests Epistemic Robustness (ER) and Intrinsic
    Hallucination integrity (IH*) via Socratic-style fabrication traps.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or os.getenv("DDFT_API_URL", "http://localhost:8002")).rstrip("/")

    def assess(
        self,
        model_name: str,
        model_config: dict,
        api_keys: dict,
        concepts: Optional[list] = None,
        compression_levels: Optional[list] = None,
    ) -> dict:
        """
        Run a DDFT cognitive assessment against a live model.

        Returns a dict with at least:
          er        — Epistemic Robustness score in [0, 1]
          ih        — Intrinsic Hallucination integrity (IH*) in [0, 1]
          ci_score  — Raw CI index
          phenotype — Cognitive phenotype label
        """
        url = f"{self.base_url}/assess"
        payload = {
            "model_name": model_name,
            "model_config": model_config,
            "api_keys": api_keys,
            "concepts": concepts or ["Natural Selection", "Recursion"],
            "compression_levels": compression_levels or [0.0, 0.5, 1.0],
        }
        logger.debug(f"[DDFT] POST {url} model={model_name}")
        return _post(url, payload)

    def get_score(self, model_name: str) -> dict:
        """
        Retrieve pre-computed ER + IH scores for *model_name*.

        Returns a dict with:
          er    — pre-computed Epistemic Robustness score
          ih    — pre-computed IH* score
          found — True if stored results exist for this model
        """
        url = f"{self.base_url}/score/{model_name}"
        logger.debug(f"[DDFT] GET {url}")
        return _get(url)


# ---------------------------------------------------------------------------
# EECT client
# ---------------------------------------------------------------------------

class EECTClient:
    """
    Client for the EECT (Ethical Emergence Comprehension Test) API service.

    The EECT service tests Behavioral Alignment Score (AS) via structured
    ethical dilemma dialogues.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or os.getenv("EECT_API_URL", "http://localhost:8003")).rstrip("/")

    def run_dialogue(
        self,
        model_name: str,
        model_config: dict,
        api_keys: dict,
        dilemma: dict,
        compression_level: str = "c1.0",
    ) -> dict:
        """
        Run a single Socratic ethical dialogue for one dilemma.

        Returns a dict with:
          turns — list of dialogue turn dicts (role, response, …)
        """
        url = f"{self.base_url}/dialogue"
        payload = {
            "model": model_name,
            "model_name": model_name,
            "model_config": model_config,
            "api_keys": api_keys,
            "dilemma_id": dilemma.get("id", ""),
            "dilemma": dilemma,
            "compression_level": compression_level,
        }
        logger.debug(f"[EECT] POST {url} model={model_name} dilemma={dilemma.get('id')}")
        return _post(url, payload)

    def get_score(self, model_name: str) -> dict:
        """
        Retrieve a pre-computed AS score for *model_name*.

        Returns a dict with:
          as_   — pre-computed Behavioral Alignment Score
          found — True if stored results exist for this model
        """
        url = f"{self.base_url}/score/{model_name}"
        logger.debug(f"[EECT] GET {url}")
        return _get(url)
