# bluesilk

A minimal personal AI agent. It runs on any tool-calling model on OpenRouter and is reached through your private Telegram chat, a built-in web console, or both: one conversation, whichever channel you pick up.

## Features

- One conversation across Telegram and the web console; the reply goes to the channel you wrote on
- Memory, reusable skills, custom tools and conversation history
- Shell access, file transfer, image understanding and background jobs
- Sub-agents: up to five helpers that report back to the agent, not to you
- Optional browser control (headless Chromium) and desktop control (X11)
- Local and remote MCP servers
- Automatic context compaction and periodic memory maintenance

## Requirements

| | |
|---|---|
| Required | Python 3.10+ on a POSIX system, an [OpenRouter API key](https://openrouter.ai/keys) |
| Telegram | A bot token from `@BotFather` and your numeric user ID (ask `@userinfobot`) |
| Browser control | Playwright and Chromium (see below) |
| Desktop control | X11 session with `xdotool` and `scrot` (or `ffmpeg`) |

## Installation

```sh
uv tool install bluesilk        # or: uv tool install 'bluesilk[browser]'
bluesilk                        # first start runs the interactive setup
```

| Command | Does |
|---|---|
| `bluesilk` | Start the agent (runs setup first if there is no config) |
| `bluesilk browser install` | Download Chromium for browser control |

Setup needs Telegram, the web console, or both. Everything lives in `~/.bluesilk/`; set `BLUESILK_HOME` to move it. To change settings later, use `/settings` in the web console, or delete `~/.bluesilk/config.json` and start again.

### Model

Setup asks for an [OpenRouter model](https://openrouter.ai/models) slug (default `deepseek/deepseek-v4.1-flash`). The model must support tool calling. Context compaction kicks in at half of the model's context window. A text-only model works, but photos and screenshots then reach the agent as file paths only, so browser and desktop control are of little use with it.

## Interfaces

### Telegram

Create a bot with `@BotFather`, press Start on it, and give setup your user ID. Messages from anyone else, and from non-private chats, are ignored.

### Web console

Listens on `http://127.0.0.1:8321/` (port configurable). There is no login: it only listens on localhost, so access is whoever can log in to your OS account. On a remote machine, tunnel it: `ssh -L 8321:127.0.0.1:8321 host`.

| Key / command | Does |
|---|---|
| `Esc` | Stop the current response |
| `Ctrl+U`, paste, drag and drop | Send a file |
| `/new` | Start a new conversation |
| `/reset` | Reset agent data (a backup is made) |
| `/settings` | Open configuration |
| `/theme` | Cycle the phosphor: blue, green, amber |

## Sub-agents

The agent can hand a task to a sub-agent (`assign`), see what it is doing (`inspect`) or stop it (`dismiss`).

- Runs on its own thread with the same tools, memory and skills
- Cannot message you or delegate further; its report goes to the main agent
- Listed in the web console above the status line
- Does not survive a restart, like background jobs

## Browser control

```sh
uv tool install 'bluesilk[browser]'
bluesilk browser install
```

Adds a `browser` tool that drives a headless Chromium from screenshots. Cookies, tabs and downloads persist under `~/.bluesilk/browser/`.

## Desktop control

```sh
sudo apt install xdotool scrot
```

On an X11 session bluesilk also gets a `desktop` tool that acts on the real screen from screenshots. Actions are serialised and the agent is told to look before it acts. Disable with `"desktop": {"enabled": false}` in the config. Wayland is not supported.

## MCP servers

Configure local stdio or remote Streamable HTTP servers in `~/.bluesilk/mcp.json`:

```json
{
  "mcpServers": {
    "local": {"command": "uv", "args": ["run", "/path/to/server.py"]},
    "remote": {"url": "https://example.com/mcp", "token": "bearer-token"}
  }
}
```

Tools appear as `<server>__<tool>`. bluesilk does not perform MCP OAuth; provide credentials the server accepts.

## Data and security

- All state is plain files under `~/.bluesilk/`: credentials, conversations, memory, uploads, job output
- The agent has unrestricted shell access as the account running it: prefer a dedicated OS account, VM or isolated machine

## Development

| Task | Command |
|---|---|
| Offline tests | `python -m unittest discover -s tests -v` |
| With Chromium tests | `uv sync --extra browser && uv run playwright install chromium && uv run python -m unittest discover -s tests -v` |
| Build | `uv build` |

## License

[MIT](LICENSE)
