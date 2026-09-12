"""Configuration: .env, model routing, thresholds, taxonomy and tenant record."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Settings:
    models: dict[str, str]
    pricing: dict[str, dict[str, float]]
    thresholds: dict[str, Any]
    taxonomy: dict[str, Any]
    tenant: dict[str, Any]
    api_key: str | None
    llm_mode: str            # "anthropic" | "mock"
    cache_dir: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def categories(self) -> dict[str, Any]:
        return self.taxonomy["categories"]

    @property
    def doc_types(self) -> list[str]:
        return list(self.taxonomy["doc_types"].keys())


def load_settings(
    env_file: str | os.PathLike | None = None,
    taxonomy_path: str | os.PathLike | None = None,
    tenant_path: str | os.PathLike | None = None,
    models_path: str | os.PathLike | None = None,
    llm_mode: str | None = None,
) -> Settings:
    """Read .env (ANTHROPIC_API_KEY lives there) and the YAML config files.

    LLM mode resolution: explicit argument > STRATA_LLM env var > "anthropic" if a key is
    present, otherwise "mock" with a warning printed by the CLI.
    """
    load_dotenv(env_file or ROOT / ".env", override=False)
    taxonomy_path = taxonomy_path or os.environ.get("STRATA_TAXONOMY") or CONFIG_DIR / "taxonomy.yaml"
    tenant_path = tenant_path or os.environ.get("STRATA_TENANT") or CONFIG_DIR / "tenant.yaml"
    models_path = models_path or os.environ.get("STRATA_MODELS") or CONFIG_DIR / "models.yaml"
    models_cfg = _load_yaml(Path(models_path))
    api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    mode = llm_mode or os.environ.get("STRATA_LLM") or ("anthropic" if api_key else "mock")
    return Settings(
        models=models_cfg["models"],
        pricing=models_cfg.get("pricing_usd_per_mtok", {}),
        thresholds=models_cfg.get("thresholds", {}),
        taxonomy=_load_yaml(Path(taxonomy_path)),
        tenant=_load_yaml(Path(tenant_path))["tenant"],
        api_key=api_key,
        llm_mode=mode,
        cache_dir=Path(os.environ.get("STRATA_CACHE_DIR", ROOT / ".cache" / "llm")),
    )
