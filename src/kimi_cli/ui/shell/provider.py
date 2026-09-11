from __future__ import annotations

from typing import cast

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from pydantic import SecretStr

from kimi_cli.config import Config, LLMModel, LLMProvider, load_config, save_config
from kimi_cli.exception import ConfigError
from kimi_cli.llm import ModelCapability
from kimi_cli.ui.shell.console import console
from kimi_cli.utils.slashcmd import SlashCommandRegistry

from .slash import ShellSlashCmdFunc, ensure_kimi_soul, registry


async def _ask(
    prompt: str,
    default: str = "",
    *,
    password: bool = False,
) -> str:
    """Ask for text without interfering with the shell prompt."""
    session: PromptSession[str] = PromptSession()

    if default:
        prompt_text = f"{prompt} [{default}]: "
    else:
        prompt_text = f"{prompt}: "

    value = await session.prompt_async(
        prompt_text,
        is_password=password,
    )

    value = value.strip()

    return value if value else default


async def _choose(
    message: str,
    options: list[tuple[str, str]],
    default: str | None = None,
) -> str | None:
    """Show an interactive selection."""
    try:
        return cast(
            str | None,
            await ChoiceInput(
                message=message,
                options=options,
                default=default,
            ).prompt_async(),
        )
    except (EOFError, KeyboardInterrupt):
        return None


def _provider_models(config: Config, provider_name: str) -> list[tuple[str, LLMModel]]:
    return [
        (name, model)
        for name, model in config.models.items()
        if model.provider == provider_name
    ]


def _print_providers(config: Config) -> None:
    console.print()
    console.print("[bold cyan]Providers configurados[/bold cyan]")
    console.print()

    if not config.providers:
        console.print("[yellow]Nenhum provider configurado.[/yellow]")
        return

    for name, provider in config.providers.items():
        models = _provider_models(config, name)

        console.print(
            f"  [bold]{name}[/bold] "
            f"[dim]({provider.type})[/dim]"
        )
        console.print(
            f"    URL: {provider.base_url}"
        )

        for model_name, model in models:
            marker = " [green]★ padrão[/green]" if (
                model_name == config.default_model
            ) else ""

            display = model.display_name or model.model

            console.print(
                f"    └─ {model_name}: {display}{marker}"
            )

        console.print()


async def _configure_carcara(config: Config) -> None:
    """Configure the built-in Carcará provider."""
    existing = config.providers.get("carcara")

    console.print()
    console.print("[bold cyan]Configurar Carcará[/bold cyan]")
    console.print()

    base_url = await _ask(
        "Base URL",
        existing.base_url if existing else "https://carcara.sinapad.lncc.br/service/v1",
    )

    current_key = (
        existing.api_key.get_secret_value()
        if existing
        else ""
    )

    api_key = await _ask(
        "API Key",
        current_key,
        password=True,
    )

    model_id = await _ask(
        "Modelo",
        "DeepSeek-v4-Flash-0731",
    )

    alias = await _ask(
        "Nome do modelo no CLI",
        "carcara/deepseek",
    )

    context_raw = await _ask(
        "Context size",
        "131072",
    )

    try:
        context_size = int(context_raw)
        if context_size <= 0:
            raise ValueError
    except ValueError:
        console.print("[red]Context size inválido.[/red]")
        return

    provider = LLMProvider(
        type="carcara",
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    model = LLMModel(
        provider="carcara",
        model=model_id,
        max_context_size=context_size,
        capabilities=cast(set[ModelCapability], {"thinking"}),
    )

    config.providers["carcara"] = provider
    config.models[alias] = model

    make_default = await _choose(
        "Definir como modelo padrão?",
        [
            ("yes", "Sim"),
            ("no", "Não"),
        ],
        default="yes",
    )

    if make_default == "yes":
        config.default_model = alias

    save_config(config)

    console.print()
    console.print("[green]✓ Carcará configurado.[/green]")
    console.print(f"[dim]Modelo: {alias}[/dim]")


async def _ollama_tags(base_url: str) -> list[str]:
    """Return locally installed Ollama models."""
    root = base_url.rstrip("/")

    if root.endswith("/v1"):
        root = root[:-3]

    if not root.endswith("/api"):
        root = f"{root}/api"

    url = f"{root}/tags"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        data = response.json()

        return [
            item["name"]
            for item in data.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]

    except (httpx.HTTPError, ValueError, KeyError):
        return []


async def _configure_ollama(config: Config) -> None:
    """Configure Ollama as an OpenAI-compatible provider."""
    existing = config.providers.get("ollama")

    console.print()
    console.print("[bold cyan]Configurar Ollama[/bold cyan]")
    console.print()

    base_url = await _ask(
        "Base URL",
        existing.base_url if existing else "http://localhost:11434/api",
    )

    console.print()
    console.print("[dim]Procurando modelos instalados no Ollama...[/dim]")

    models = await _ollama_tags(base_url)

    if models:
        console.print()
        console.print("[green]✓ Ollama encontrado.[/green]")
        console.print()

        options = [
            (model, model)
            for model in models
        ]

        selected = await _choose(
            "Selecione o modelo:",
            options,
            default=models[0],
        )

        if not selected:
            return

        model_id = selected

    else:
        console.print(
            "[yellow]Nenhum modelo foi detectado no Ollama.[/yellow]"
        )

        model_id = await _ask(
            "Nome do modelo",
            "qwen3:8b",
        )

    alias = await _ask(
        "Nome do modelo no CLI",
        f"ollama/{model_id}",
    )

    context_raw = await _ask(
        "Context size",
        "32768",
    )

    try:
        context_size = int(context_raw)
        if context_size <= 0:
            raise ValueError
    except ValueError:
        console.print("[red]Context size inválido.[/red]")
        return

    # Ollama uses its native API (base_url http://localhost:11434/api).
    # The native OllamaProvider (src/kimi_cli/ollama.py) supports tool calling
    # and is what create_llm() builds for type="ollama".
    provider = LLMProvider(
        type="ollama",
        base_url=base_url,
        api_key=SecretStr("ollama"),
    )

    model = LLMModel(
        provider="ollama",
        model=model_id,
        max_context_size=context_size,
        capabilities=set(),
    )

    config.providers["ollama"] = provider
    config.models[alias] = model

    make_default = await _choose(
        "Definir como modelo padrão?",
        [
            ("yes", "Sim"),
            ("no", "Não"),
        ],
        default="yes",
    )

    if make_default == "yes":
        config.default_model = alias

    save_config(config)

    console.print()
    console.print("[green]✓ Ollama configurado.[/green]")
    console.print(f"[dim]Modelo: {alias}[/dim]")


async def _configure_manual(config: Config) -> None:
    """Configure an arbitrary OpenAI-compatible provider."""
    console.print()
    console.print("[bold cyan]Novo provider manual[/bold cyan]")
    console.print(
        "[dim]Funciona com APIs compatíveis com OpenAI, "
        "como OpenRouter, DeepSeek, LM Studio, vLLM etc.[/dim]"
    )
    console.print()

    name = await _ask(
        "Nome do provider",
    )

    if not name:
        console.print("[red]O nome é obrigatório.[/red]")
        return

    if any(char.isspace() for char in name):
        console.print(
            "[red]O nome do provider não pode conter espaços.[/red]"
        )
        return

    base_url = await _ask(
        "Base URL",
    )

    if not base_url:
        console.print("[red]A Base URL é obrigatória.[/red]")
        return

    api_key = await _ask(
        "API Key",
        password=True,
    )

    model_id = await _ask(
        "Modelo",
    )

    if not model_id:
        console.print("[red]O modelo é obrigatório.[/red]")
        return

    alias = await _ask(
        "Nome do modelo no CLI",
        f"{name}/{model_id}",
    )

    context_raw = await _ask(
        "Context size",
        "32768",
    )

    try:
        context_size = int(context_raw)
        if context_size <= 0:
            raise ValueError
    except ValueError:
        console.print("[red]Context size inválido.[/red]")
        return

    thinking = await _choose(
        "O modelo suporta thinking/reasoning?",
        [
            ("no", "Não"),
            ("yes", "Sim"),
        ],
        default="no",
    )

    capabilities: set[ModelCapability] = set()

    if thinking == "yes":
        capabilities.add("thinking")

    provider = LLMProvider(
        type="openai_legacy",
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    model = LLMModel(
        provider=name,
        model=model_id,
        max_context_size=context_size,
        capabilities=capabilities,
    )

    config.providers[name] = provider
    config.models[alias] = model

    make_default = await _choose(
        "Definir como modelo padrão?",
        [
            ("yes", "Sim"),
            ("no", "Não"),
        ],
        default="yes",
    )

    if make_default == "yes":
        config.default_model = alias

    save_config(config)

    console.print()
    console.print(
        f"[green]✓ Provider '{name}' configurado.[/green]"
    )
    console.print(f"[dim]Modelo: {alias}[/dim]")


async def _add_provider(config: Config) -> None:
    choice = await _choose(
        "Escolha o provider:",
        [
            (
                "carcara",
                "Carcará — LNCC / Santos Dumont",
            ),
            (
                "ollama",
                "Ollama — modelos locais",
            ),
            (
                "manual",
                "Manual — OpenAI-compatible",
            ),
            (
                "cancel",
                "Cancelar",
            ),
        ],
        default="carcara",
    )

    if choice is None or choice == "cancel":
        return

    if choice == "carcara":
        await _configure_carcara(config)
    elif choice == "ollama":
        await _configure_ollama(config)
    elif choice == "manual":
        await _configure_manual(config)


async def _set_default(config: Config) -> None:
    if not config.models:
        console.print("[yellow]Nenhum modelo configurado.[/yellow]")
        return

    options: list[tuple[str, str]] = []

    for name, model in sorted(config.models.items()):
        provider = config.providers.get(model.provider)

        provider_type = (
            provider.type
            if provider is not None
            else "unknown"
        )

        marker = " ← atual" if name == config.default_model else ""

        options.append(
            (
                name,
                f"{name} — {provider_type}{marker}",
            )
        )

    selected = await _choose(
        "Selecione o modelo padrão:",
        options,
        default=config.default_model or options[0][0],
    )

    if not selected:
        return

    config.default_model = selected
    save_config(config)

    console.print(
        f"[green]✓ Modelo padrão: {selected}[/green]"
    )


async def _remove_provider(config: Config) -> None:
    if not config.providers:
        console.print("[yellow]Nenhum provider configurado.[/yellow]")
        return

    options = [
        (
            name,
            f"{name} ({provider.type})",
        )
        for name, provider in sorted(config.providers.items())
    ]

    options.append(
        ("cancel", "Cancelar"),
    )

    selected = await _choose(
        "Selecione o provider para remover:",
        options,
        default="cancel",
    )

    if not selected or selected == "cancel":
        return

    models = _provider_models(config, selected)

    if models:
        console.print()
        console.print(
            f"[yellow]Os seguintes modelos serão removidos:[/yellow]"
        )

        for model_name, _ in models:
            console.print(f"  - {model_name}")

    confirm = await _choose(
        f"Remover '{selected}'?",
        [
            ("yes", "Sim, remover"),
            ("no", "Cancelar"),
        ],
        default="no",
    )

    if confirm != "yes":
        return

    for model_name, _ in models:
        del config.models[model_name]

    del config.providers[selected]

    if config.default_model not in config.models:
        config.default_model = (
            next(iter(config.models))
            if config.models
            else ""
        )

    save_config(config)

    console.print(
        f"[green]✓ Provider '{selected}' removido.[/green]"
    )


async def _provider_menu(app, config: Config) -> None:
    while True:
        console.clear()

        console.print(
            "[bold cyan]╭────────────────────────────────────╮[/bold cyan]"
        )
        console.print(
            "[bold cyan]│       Carcará Provider Manager     │[/bold cyan]"
        )
        console.print(
            "[bold cyan]╰────────────────────────────────────╯[/bold cyan]"
        )

        _print_providers(config)

        choice = await _choose(
            "Provider:",
            [
                ("add", "Adicionar provider"),
                ("default", "Definir modelo padrão"),
                ("remove", "Remover provider"),
                ("reload", "Recarregar config.toml"),
                ("exit", "Sair"),
            ],
            default="add",
        )

        if choice is None or choice == "exit":
            return

        if choice == "add":
            await _add_provider(config)

        elif choice == "default":
            await _set_default(config)

        elif choice == "remove":
            await _remove_provider(config)

        elif choice == "reload":
            try:
                new_config = load_config(
                    config.source_file
                )

                config.providers = new_config.providers
                config.models = new_config.models
                config.default_model = new_config.default_model
                config.default_thinking = new_config.default_thinking

                console.print(
                    "[green]✓ config.toml recarregado.[/green]"
                )

            except ConfigError as exc:
                console.print(
                    f"[red]Erro ao carregar config.toml: {exc}[/red]"
                )

        console.print()
        console.print(
            "[dim]Pressione Enter para continuar...[/dim]"
        )

        try:
            await PromptSession().prompt_async("")
        except (EOFError, KeyboardInterrupt):
            return


@registry.command
async def provider(app, args: str):
    """Configure LLM providers and API connections."""
    soul = ensure_kimi_soul(app)

    if soul is None:
        return

    config = soul.runtime.config

    if not config.is_from_default_location:
        console.print(
            "[yellow]A configuração de providers precisa usar "
            "o config.toml padrão.[/yellow]"
        )
        console.print(
            "[dim]Reinicie o Carcará sem --config/--config-file.[/dim]"
        )
        return

    await _provider_menu(app, config)