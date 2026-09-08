"""Provider switching and fallback functionality.

Provides manual provider switching via /provider slash command and
automatic fallback when the current provider becomes unavailable.
Also includes auto-discovery of local providers (Ollama, llama.cpp).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from kosong.message import Message

from kimi_cli import logger
from kimi_cli.soul import wire_send
from kimi_cli.soul.message import system
from kimi_cli.wire.types import TextPart
from kimi_cli.utils.slashcmd import SlashCommandRegistry

if TYPE_CHECKING:
    from kimi_cli.soul.kimisoul import KimiSoul

type SoulSlashCmdFunc = callable[[KimiSoul, str], None]

registry = SlashCommandRegistry[SoulSlashCmdFunc]()


async def _discover_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Discover available models from Ollama server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = [model["name"] for model in data.get("models", [])]
                logger.info("Discovered {count} Ollama models: {models}", count=len(models), models=models)
                return models
    except Exception as e:
        logger.debug("Ollama not available at {url}: {error}", url=base_url, error=e)
    return []


async def _discover_llamacpp_models(base_url: str = "http://localhost:8080") -> list[str]:
    """Discover available models from llama.cpp server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/completion")
            # llama.cpp doesn't have a direct models endpoint, but we can check if it's running
            # Usually returns 400 or 405, but connection success means it's up
            if response.status_code in [400, 405, 200]:
                logger.info("llama.cpp server detected at {url}", url=base_url)
                return ["default"]  # llama.cpp typically serves one model at a time
    except Exception as e:
        logger.debug("llama.cpp not available at {url}: {error}", url=base_url, error=e)
    return []


async def discover_local_providers(config) -> dict[str, dict]:
    """Auto-discover local providers and their models.
    
    Returns a dict of discovered providers with their models.
    These are merged with configured providers for display/usage.
    """
    discovered = {}
    
    # Check Ollama
    ollama_models = await _discover_ollama_models()
    if ollama_models:
        discovered["ollama"] = {
            "type": "ollama",
            "base_url": "http://localhost:11434",
            "models": ollama_models,
            "auto_discovered": True
        }
    
    # Check llama.cpp
    llamacpp_models = await _discover_llamacpp_models()
    if llamacpp_models:
        discovered["llama_cpp"] = {
            "type": "llama_cpp",
            "base_url": "http://localhost:8080",
            "models": llamacpp_models,
            "auto_discovered": True
        }
    
    return discovered


@registry.command
async def provider(soul: KimiSoul, args: str):
    """Switch to a different provider/model. Usage: /provider <provider_name> or /provider <model_alias>
    
    Examples:
        /provider ollama          - Switch to Ollama provider (first available model)
        /provider llama_cpp       - Switch to llama.cpp provider
        /provider carcara         - Switch back to Carcará
        /provider ollama/llama3   - Switch to specific model alias
    """
    from kimi_cli.config import save_config
    from kimi_cli.llm import create_llm
    
    # Auto-discover local providers
    discovered = await discover_local_providers(soul.runtime.config)
    
    args = args.strip()
    if not args:
        # List available providers and models (configured + discovered)
        lines = ["Available providers and models:"]
        
        # Show configured providers
        for provider_name, provider_cfg in soul.runtime.config.providers.items():
            lines.append(f"\n  **{provider_name}** ({provider_cfg.type})")
            lines.append(f"     URL: {provider_cfg.base_url}")
            for alias, model_cfg in soul.runtime.config.models.items():
                if model_cfg.provider == provider_name:
                    marker = "★ " if alias == soul.runtime.config.default_model else "  "
                    lines.append(f"     {marker}{alias}: {model_cfg.model}")
        
        # Show auto-discovered providers
        for provider_name, provider_info in discovered.items():
            if provider_name not in soul.runtime.config.providers:
                lines.append(f"\n  **{provider_name}** ({provider_info['type']}) [AUTO-DISCOVERED]")
                lines.append(f"     URL: {provider_info['base_url']}")
                for model in provider_info['models']:
                    lines.append(f"       • {model}")
        
        current_provider = None
        if soul.runtime.llm and soul.runtime.llm.provider_config:
            current_provider = soul.runtime.llm.provider_config.type
        
        lines.append(f"\nCurrent provider: **{current_provider}**")
        lines.append("\nUsage: /provider <provider_name> or /provider <model_alias>")
        wire_send(TextPart(text="\n".join(lines)))
        return
    
    config = soul.runtime.config
    current_llm = soul.runtime.llm
    
    # Check if arg is a model alias first
    if args in config.models:
        target_alias = args
        target_model = config.models[target_alias]
        target_provider = config.providers.get(target_model.provider)
        
        if not target_provider:
            wire_send(TextPart(text=f"Provider '{target_model.provider}' not found in config."))
            return
        
        try:
            new_llm = create_llm(
                target_provider,
                target_model,
                thinking=getattr(current_llm.chat_provider, "thinking_effort", None) != "off" if current_llm else None,
                session_id=soul.runtime.session.id,
                oauth=soul.runtime.oauth_manager,
            )
            
            if new_llm is None:
                wire_send(TextPart(text=f"Failed to create LLM for model '{target_alias}'."))
                return
            
            # Update runtime
            soul.runtime.llm = new_llm
            soul.runtime.config.default_model = target_alias
            
            # Persist to config file
            if config.source_file:
                try:
                    config_for_save = soul.runtime.config.__class__.model_validate(config.model_dump())
                    config_for_save.default_model = target_alias
                    save_config(config_for_save, config.source_file)
                except Exception as e:
                    logger.warning("Failed to persist default_model: {error}", error=e)
            
            wire_send(TextPart(text=f"Switched to model: **{target_alias}** ({target_model.model})"))
            wire_send(TextPart(text=f"Provider: **{target_provider.type}** @ {target_provider.base_url}"))
            
            # Inject system message to inform LLM
            system_message = system(
                f"User switched to model '{target_alias}' ({target_model.model}) "
                f"using provider '{target_provider.type}'."
            )
            await soul.context.append_message(Message(role="user", content=[system_message]))
            
        except Exception as e:
            logger.exception("Failed to switch model")
            wire_send(TextPart(text=f"Error switching to model '{target_alias}': {e}"))
        return
    
    # Check if arg is a provider name
    if args in config.providers:
        provider_name = args
        provider_cfg = config.providers[provider_name]
        
        # Find first available model for this provider
        available_models = [
            alias for alias, model_cfg in config.models.items()
            if model_cfg.provider == provider_name
        ]
        
        if not available_models:
            wire_send(TextPart(
                text=f"No models configured for provider '{provider_name}'. "
                     f"Add a model with provider='{provider_name}' to your config."
            ))
            return
        
        target_alias = available_models[0]
        target_model = config.models[target_alias]
        
        try:
            new_llm = create_llm(
                provider_cfg,
                target_model,
                thinking=getattr(current_llm.chat_provider, "thinking_effort", None) != "off" if current_llm else None,
                session_id=soul.runtime.session.id,
                oauth=soul.runtime.oauth_manager,
            )
            
            if new_llm is None:
                wire_send(TextPart(text=f"Failed to create LLM for provider '{provider_name}'."))
                return
            
            # Update runtime
            soul.runtime.llm = new_llm
            soul.runtime.config.default_model = target_alias
            
            # Persist to config file
            if config.source_file:
                try:
                    config_for_save = soul.runtime.config.__class__.model_validate(config.model_dump())
                    config_for_save.default_model = target_alias
                    save_config(config_for_save, config.source_file)
                except Exception as e:
                    logger.warning("Failed to persist default_model: {error}", error=e)
            
            wire_send(TextPart(text=f"Switched to provider: **{provider_name}**"))
            wire_send(TextPart(text=f"Model: **{target_alias}** ({target_model.model})"))
            wire_send(TextPart(text=f"URL: {provider_cfg.base_url}"))
            
            # Inject system message to inform LLM
            system_message = system(
                f"User switched to provider '{provider_name}' using model '{target_alias}'."
            )
            await soul.context.append_message(Message(role="user", content=[system_message]))
            
        except Exception as e:
            logger.exception("Failed to switch provider")
            wire_send(TextPart(text=f"Error switching to provider '{provider_name}': {e}"))
        return
    
    # Neither model nor provider found
    wire_send(TextPart(
        text=f"Unknown provider or model alias: '{args}'\n"
             f"Run /provider without arguments to see available options."
    ))


async def automatic_fallback(
    soul: KimiSoul,
    error: Exception | None = None,
) -> bool:
    """Attempt automatic fallback to alternative providers when current fails.
    
    This is called when the current provider returns errors or becomes unavailable.
    It tries providers in order: ollama -> llama_cpp -> any other available provider.
    
    Returns:
        True if fallback was successful, False otherwise.
    """
    from kimi_cli.llm import create_llm
    
    config = soul.runtime.config
    current_llm = soul.runtime.llm
    
    if not current_llm or not current_llm.provider_config:
        return False
    
    current_provider_type = current_llm.provider_config.type
    logger.warning(
        "Automatic fallback triggered due to provider issues: {error}",
        error=str(error) if error else "unavailable"
    )
    
    # Define fallback priority order
    fallback_order = ["ollama", "llama_cpp"]
    
    # Add any other providers that aren't the current one
    for provider_name in config.providers:
        if provider_name not in fallback_order and provider_name != current_provider_type:
            fallback_order.append(provider_name)
    
    # Try each fallback provider
    for provider_name in fallback_order:
        if provider_name not in config.providers:
            continue
        
        provider_cfg = config.providers[provider_name]
        
        # Find first model for this provider
        available_models = [
            alias for alias, model_cfg in config.models.items()
            if model_cfg.provider == provider_name
        ]
        
        if not available_models:
            logger.debug("Skipping provider {name}: no models configured", name=provider_name)
            continue
        
        target_alias = available_models[0]
        target_model = config.models[target_alias]
        
        try:
            logger.info("Attempting fallback to provider: {name}", name=provider_name)
            new_llm = create_llm(
                provider_cfg,
                target_model,
                thinking=getattr(current_llm.chat_provider, "thinking_effort", None) != "off",
                session_id=soul.runtime.session.id,
                oauth=soul.runtime.oauth_manager,
            )
            
            if new_llm is None:
                logger.warning("Fallback failed: could not create LLM for {name}", name=provider_name)
                continue
            
            # Success! Update runtime
            soul.runtime.llm = new_llm
            old_provider = current_provider_type
            soul.runtime.config.default_model = target_alias
            
            wire_send(TextPart(
                text=f"⚠️ **Auto-fallback activated**: Provider '{old_provider}' failed.\n"
                     f"Switched to '{provider_name}' ({target_alias})."
            ))
            
            # Inject system message
            system_message = system(
                f"System automatically switched from '{old_provider}' to '{provider_name}' "
                f"due to availability issues. Using model '{target_alias}'."
            )
            await soul.context.append_message(Message(role="user", content=[system_message]))
            
            logger.info(
                "Successfully fell back from {old} to {new} ({model})",
                old=old_provider,
                new=provider_name,
                model=target_alias
            )
            return True
            
        except Exception as e:
            logger.warning(
                "Fallback to {name} failed: {error}",
                name=provider_name,
                error=e
            )
            continue
    
    # All fallbacks failed
    wire_send(TextPart(
        text="❌ **All fallback providers unavailable**. Please check your configuration."
    ))
    return False
