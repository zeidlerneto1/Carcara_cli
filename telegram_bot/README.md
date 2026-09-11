# Carcará Telegram Bot

Um bot Telegram que embute o **Kimi Code CLI (Carcará)** como agente, dando uma
experiência semelhante ao CLI via Telegram: respostas em streaming, execução de
ferramentas, aprovações e perguntas interativas.

## Como funciona

Cada usuário do Telegram tem seu próprio diretório de trabalho isolado
(`~/.carcara_telegram/<chat_id>/`) e sua própria sessão persistida. O bot usa a
API programática `KimiCLI.run()` do `kimi_cli` para executar cada turno e
converte os `WireMessage`s em ações do Telegram.

## Configuração

O bot reutiliza o config do Carcará (`~/.kimi/config.toml` por padrão, que já
contém o provider e a autenticação). Variáveis de ambiente:

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `TELEGRAM_BOT_TOKEN` | _(obrigatório)_ | Token do @BotFather |
| `CARCARA_TG_BASE_DIR` | `~/.carcara_telegram` | Diretório base por usuário |
| `CARCARA_CONFIG_FILE` | _(default do kimi)_ | Caminho para um config.toml alternativo |
| `CARCARA_MODEL` | _(default do config)_ | Nome do modelo |
| `CARCARA_SHOW_THINKING` | `false` | Exibir raciocínio (ThinkPart) |
| `CARCARA_DEFAULT_APPROVAL` | `inline` | `inline` ou `yolo` para novos usuários |
| `CARCARA_STREAM_REFRESH` | `0.6` | Segundos entre atualizações do streaming |
| `CARCARA_STREAM_MAX_CHARS` | `3500` | Máx. de chars antes de quebrar mensagem |
| `CARCARA_APPROVAL_TIMEOUT` | `30` | Segundos para auto-rejeitar aprovação/questão (máx. 30; 0 = nunca) |
| `CARCARA_ADMIN_IDS` | _(vazio)_ | IDs de usuários Telegram (separados por vírgula) com permissão para comandos privilegiados, ex.: `/restart` |

## Rodando

```sh
# dentro do diretório do bot
cd telegram_bot
uv sync
TELEGRAM_BOT_TOKEN=<token> uv run python -m telegram_bot
```

## Comandos

- `/start`, `/help` — ajuda
- `/new` — nova sessão (limpa contexto)
- `/model` — trocar o modelo em uso (lista os disponíveis no config, contexto preservado)
- `/think` — ligar/desligar o modo de pensamento (só se o modelo atual suportar)
- `/status` — mostrar modelo, pensamento e aprovação atuais
- `/yolo` — alternar aprovação automática (on/off)
- `/cancel` — cancelar turno em andamento
- `/clear` — limpar contexto (delegado ao agente)
- `/restart` — reiniciar o bot (somente admin, via `CARCARA_ADMIN_IDS`)

## Trocando de modelo e recursos

O bot lê os modelos do config do Carcará (`~/.kimi/config.toml`) e permite trocar
em runtime, **sem perder o contexto da conversa**:

- `/model` — abre um teclado inline com todos os modelos do config. Ao clicar,
o `KimiCLI` troca o LLM ativo na sessão (a mesma técnica do model-fallback).
- `/think` — liga/desliga o modo de pensamento. Só funciona se o modelo atual
tenha a capability `thinking`; caso contrário o bot avisa.
- `/status` — mostra modelo ativo, estado do pensamento e modo de aprovação.

Os recursos suportados por cada modelo vêm de `LLMModel.capabilities` no config
(`thinking`, `image_in`, `video_in`, `always_thinking`). Modelos sem suporte a
thinking aparecem rotulados "(sem thinking)" no menu de `/model`.

## Voz

O bot aceita mensagens de voz (🎤). A transcrição é feita localmente com o modelo
VOSK PT-BR (`models/vosk-model-small-pt-0.3/`), sem necessidade de internet.

## Imagem e vídeo

O bot aceita fotos (🖼️), vídeos (🎬) e documentos de imagem/vídeo. O arquivo é
baixado via Bot API e enviado ao agente como data-URI (`ImageURLPart` / `VideoURLPart`).

**Suporte depende do modelo ativo:** o modelo precisa ter a capability
`image_in` (para imagens) ou `video_in` (para vídeos) no config. Se o modelo
corrente não suportar, o bot avisa e pede pra trocar com `/model`. Legendas
(captions) são incluídas como texto junto com a mídia.

Comandos do agente (`/compact`, `/plan`, `/skill:...`, `/flow:...`) são passados
diretamente ao agente.

## Scripts

```sh
# Iniciar em background (nohup + setsid)
./start.sh

# Reiniciar (mata o processo atual e relança)
./restart.sh
```

## Testes

```sh
uv sync --all-groups
uv run python -m pytest tests/ -q
```

## Estrutura

```
telegram_bot/
├── pyproject.toml
├── start.sh              # inicia em background
├── restart.sh            # mata + relança
├── logs/bot.log          # log do bot
├── models/vosk-model-small-pt-0.3/  # modelo VOSK PT-BR
├── src/telegram_bot/
│   ├── __main__.py       # entry point
│   ├── bot.py            # Application, handlers, comandos
│   ├── config.py         # configuração
│   ├── session_manager.py# KimiCLI por chat
│   ├── runner.py         # consome WireMessages e streama para o Telegram
│   ├── approvals.py      # botões de aprovação
│   ├── questions.py      # botões de perguntas
│   ├── model_switch.py   # troca de modelo in-place + /model /think /status
│   ├── media.py          # baixa imagem/vídeo do Telegram (data-URI) p/ o agente
│   ├── rendering.py      # formatação para o Telegram
│   └── audio.py          # transcrição de voz (VOSK)
└── tests/                # testes unitários
```
