# Carcará CLI

Carcará CLI é um agente de IA para terminal, adaptado do [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) para funcionar com o servidor **Carcará** do LNCC (Laboratório Nacional de Computação Científica), rodando sobre o supercomputador **Santos Dumont**.

## O que é o Carcará

O **Carcará** é o serviço de LLM open-source do LNCC, servido via `llama.cpp` em nós GPU do supercomputador Santos Dumont (SINAPAD). O Carcará CLI conecta-se diretamente a este servidor, permitindo que você use modelos como o **DeepSeek-v4-Flash-0731** e **Qwen3.8-27B** diretamente do terminal.

## Recursos

- 🧠 **Chat interativo** no terminal com streaming em tempo real
- 🔧 **Modo Agente** — executa comandos de shell, lê/edita arquivos, busca na web
- 🛠️ **Tool calling nativo** — `shell`, `read_file`, `write_file`, `grep`, `fetch_url`, etc.
- ⚡ **Controle fino de sampling** — temperature, top_k, top_p, min_p, XTC, backend sampling
- 🎯 **Thinking modes** — off, low, medium, high, max (com `thinking_budget_tokens`)
- 🌐 **Suporte a tools do LNCC** — `get_environment`, `list_skills`, `get_skill`, `ask_expert`

---

## Instalação

### Pré-requisitos

- **Git** — para clonar o repositório
- **Python 3.12+** — o projeto usa Python moderno
- **uv** — package manager (instalado automaticamente nos passos abaixo)

---

### Windows

#### Passo 1: Instalar `uv`

Abra o **PowerShell como Administrador** e execute:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Feche e reabra o PowerShell. Teste:

```powershell
uv --version
```

#### Passo 2: Clonar o repositório

```powershell
git clone https://github.com/zeidlerneto1/Carcara_cli.git
cd Carcara_cli
```

#### Passo 3: Instalar dependências

```powershell
uv sync --all-extras --all-packages
```

Isso instala todos os pacotes do workspace (`kosong`, `kimi-code`, `kimi-cli`) + dependências.

#### Passo 4: Configurar

Crie o arquivo de configuração:

```powershell
mkdir "$env:USERPROFILE\.kimi"
```

Crie o arquivo `config.toml` **sem BOM** (use o Notepad e salve como UTF-8, ou execute o comando abaixo):

```powershell
$path = "$env:USERPROFILE\.kimi\config.toml"
$bytes = [System.Text.Encoding]::UTF8.GetBytes(@'
default_model = "carcara/deepseek"

[providers.carcara]
type = "carcara"
base_url = "https://carcara.sinapad.lncc.br/service/v1"
api_key = ""

[models."carcara/deepseek"]
provider = "carcara"
model = "DeepSeek-v4-Flash-0731"
max_context_size = 131072
capabilities = ["thinking"]
'@)
[System.IO.File]::WriteAllBytes($path, $bytes)
```

#### Passo 5: Testar

```powershell
uv run kimi -p "teste de conexão com o carcará"
```

Para entrar no modo interativo:

```powershell
uv run kimi
```

---

### Linux

#### Passo 1: Instalar `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Recarregue o shell:

```bash
source $HOME/.local/bin/env  # ou reinicie o terminal
uv --version
```

#### Passo 2: Clonar o repositório

```bash
git clone https://github.com/zeidlerneto1/Carcara_cli.git
cd Carcara_cli
```

#### Passo 3: Instalar dependências

```bash
uv sync --all-extras --all-packages
```

#### Passo 4: Configurar

```bash
mkdir -p ~/.kimi
cat > ~/.kimi/config.toml << 'EOF'
default_model = "carcara/deepseek"

[providers.carcara]
type = "carcara"
base_url = "https://carcara.sinapad.lncc.br/service/v1"
api_key = ""

[models."carcara/deepseek"]
provider = "carcara"
model = "DeepSeek-v4-Flash-0731"
max_context_size = 131072
capabilities = ["thinking"]
EOF
```

#### Passo 5: Testar

```bash
uv run kimi -p "teste de conexão com o carcará"
```

Para entrar no modo interativo:

```bash
uv run kimi
```

---

## Uso

### Modos de operação

```bash
# Chat interativo
uv run kimi

# Comando único
uv run kimi -p "explique este código"

# Modo Agente (auto-aprovação)
uv run kimi --yolo -p "refatore o projeto para usar asyncio"

# Modo Plano
uv run kimi --plan -p "adicione autenticação JWT"

# Background
uv run kimi --background -p "faça um relatório dos commits"

# Print (output puro, sem TUI)
uv run kimi --print -p "gera um Dockerfile" > Dockerfile
```

### Agentes embutidos

Você pode escolher um agente embutido na inicialização com a flag `--agent`:

| Agente | Descrição |
|--------|-----------|
| `default` | Agente padrão, adequado para uso geral. |
| `okabe` | Agente experimental para testar novos prompts e ferramentas. |
| `jarvis` | Assistente pessoal com system prompt próprio (persona ENI), herda todas as ferramentas do `default`. |

```bash
# Agente padrão (default)
uv run kimi

# Agente experimental okabe
uv run kimi --agent okabe -p "teste"

# Agente jarvis (assistente pessoal)
uv run kimi --agent jarvis -p "oi"
```

### Thinking modes

```bash
# Off (padrão)
uv run kimi -p "resposta direta"

# Low thinking (512 tokens)
uv run kimi --thinking low -p "pense um pouco"

# Medium (2048 tokens)
uv run kimi --thinking medium -p "análise profunda"

# High (8192 tokens)
uv run kimi --thinking high -p "problema complexo"

# Max (ilimitado)
uv run kimi --thinking max -p "vá o mais fundo possível"
```

### Sampling params (via env vars)

```bash
export CARCARA_TEMPERATURE=0.7
export CARCARA_TOP_K=40
export CARCARA_TOP_P=0.95
export CARCARA_MIN_P=0.05
export CARCARA_BACKEND_SAMPLING=true

uv run kimi -p "teste com sampling customizado"
```

| Env Var | Tipo | Descrição |
|---------|------|-----------|
| `CARCARA_TEMPERATURE` | float | Criatividade (0 = determinístico, >1 = aleatório) |
| `CARCARA_DYNATEMP_RANGE` | float | Variação dinâmica de temp |
| `CARCARA_DYNATEMP_EXPONENT` | float | Expoente do dynatemp |
| `CARCARA_TOP_K` | int | Mantém apenas K tokens mais prováveis |
| `CARCARA_TOP_P` | float | Nucleus sampling (0.0–1.0) |
| `CARCARA_MIN_P` | float | Prob mínima relativa ao token top |
| `CARCARA_XTC_PROBABILITY` | float | Chance de ativar XTC sampler |
| `CARCARA_XTC_THRESHOLD` | float | Threshold do XTC |
| `CARCARA_TYP_P` | float | Typical sampling p |
| `CARCARA_BACKEND_SAMPLING` | bool | `true` = samplers na GPU |

### Tools do LNCC (MCP)

Por padrão, as tools do MCP do LNCC estão **desativadas**. Para ativar:

```bash
# Linux
export CARCARA_LNCC_TOOLS=true

# Windows (PowerShell)
$env:CARCARA_LNCC_TOOLS = "true"

uv run kimi -p "liste as skills disponíveis no LNCC"
```

Com isso ativado, o modelo pode usar:
- `get_environment` — contexto do SDumont
- `list_skills` — lista skills do domínio LNCC
- `get_skill` — pega conhecimento de uma skill
- `ask_expert` — consulta especialista

---

## Múltiplos modelos

Você pode cadastrar vários modelos no `config.toml`:

```toml
default_model = "carcara/deepseek"

[providers.carcara]
type = "carcara"
base_url = "https://carcara.sinapad.lncc.br/service/v1"
api_key = ""

[models."carcara/deepseek"]
provider = "carcara"
model = "DeepSeek-v4-Flash-0731"
max_context_size = 131072
capabilities = ["thinking"]

[models."carcara/qwen"]
provider = "carcara"
model = "Qwen3.8-27B"
max_context_size = 32768
capabilities = ["thinking"]
```

Trocar durante a sessão:

```bash
uv run kimi /model carcara/qwen
```

---

## Solução de problemas

### Erro: `Empty key at line 1 col 0`

O arquivo `config.toml` foi salvo com BOM. Recrie sem BOM:

**Windows:**
```powershell
$path = "$env:USERPROFILE\.kimi\config.toml"
$bytes = [System.Text.Encoding]::UTF8.GetBytes((Get-Content $path -Raw))
[System.IO.File]::WriteAllBytes($path, $bytes)
```

**Linux:**
```bash
sed -i "1s/^\xEF\xBB\xBF//" ~/.kimi/config.toml
```

### Erro: `503 Service Unavailable`

O servidor Carcará está sobrecarregado ou em manutenção. Aguarde alguns segundos e tente novamente.

### Erro: `cannot access local variable chat_provider`

O `llm.py` não reconhece o provider `carcara`. Atualize o repositório:

```bash
git pull origin main
```

---

## Créditos

- Base: [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) — Moonshot AI
- Servidor: [Carcará](https://carcara.sinapad.lncc.br) — LNCC / SINAPAD / Santos Dumont
- Modelo: [DeepSeek-V4-Flash-0731](https://huggingface.co/deepseek-ai) — DeepSeek AI / Unsloth

## Licença

MIT License — veja [LICENSE](LICENSE) para detalhes.