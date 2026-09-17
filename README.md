# bluesilk

A fast, minimal personal AI agent. bluesilk runs on any tool-calling model on OpenRouter and is reached through your private Telegram chat, a built-in web console, or both: one conversation, whichever channel you pick up.

## Features

- One conversation across Telegram and the web console; the reply goes to the channel you wrote on
- Memory, reusable skills, custom tools, and conversation history
- Built-in shell access, file transfer, image understanding, and background jobs
- Optional Chromium control through screenshots and mouse/keyboard actions
- Local and remote MCP server support
- Automatic context compaction and periodic memory maintenance

## Requirements

- Python 3.10 or newer on a POSIX system
- An [OpenRouter API key](https://openrouter.ai/keys)
- Optional: a Telegram bot token and your Telegram user ID
- Optional: Playwright and Chromium for browser control
- Optional: `xdotool` and `scrot` (or `ffmpeg`) on an X11 desktop for desktop control

## Installation

Using `uv`:

```sh
uv tool install bluesilk
```

Or using `pipx`:

```sh
pipx install bluesilk
```

Start the agent and follow the interactive setup:

```sh
bluesilk
```

You can run setup again from the terminal, or change settings from the web console (`/settings`):

```sh
bluesilk setup
```

The setup requires Telegram, the web console, or both. Configuration and persistent data are stored in `~/.bluesilk/` by default. Set `BLUESILK_HOME` to use another directory.

### Model

Setup asks for an [OpenRouter model](https://openrouter.ai/models) slug; the default is `deepseek/deepseek-v4.1-flash`. The model must support tool calling. Context compaction is set to half of the model's context window. A text-only model still works, but photos and screenshots then reach the agent as file paths only, so browser and desktop control are of little use with it.

Upgrading from 0.6 or earlier: the stored key was for DeepSeek's own API. bluesilk runs setup again on the next start; enter an OpenRouter key.

Upgrading from 0.8 or earlier: bluesilk was built for a small team; 0.9 is single-user. The first Telegram member of the old config becomes the user, web members and passwords are dropped, and the conversation starts fresh (memory, skills, tools and `history.jsonl` are kept).

## Interfaces

### Telegram

Create a bot with `@BotFather`, press Start on it, and give setup your numeric Telegram user ID (ask `@userinfobot`). Messages from anyone else, and from non-private chats, are ignored.

### Web console

The web console listens on `http://127.0.0.1:8321/` by default (the port is configurable). There is no login: it only listens on localhost, so access is whoever can log in to your OS account. For a remote machine, use an SSH tunnel (`ssh -L 8321:127.0.0.1:8321 host`).

- `Esc` stops the current response.
- `Ctrl+U`, paste, or drag and drop sends a file.
- `/new` starts a new conversation.
- `/reset` resets agent data and creates a backup.
- `/settings` opens configuration.
- `/theme` cycles the phosphor: blue, green, amber.

## Browser control

Install the optional browser support and Chromium:

```sh
uv tool install 'bluesilk[browser]'
bluesilk browser install
```

This adds a `browser` tool that drives a headless Chromium from screenshots. Cookies, tabs, and downloads persist under `~/.bluesilk/browser/`.

## Desktop control

On an X11 session with `xdotool` and `scrot` (or `ffmpeg`) installed, bluesilk also gets a `desktop` tool that acts on the real screen from screenshots:

```sh
sudo apt install xdotool scrot
```

It is your real screen, so actions are serialised and the agent is told to look before it acts. Set `"desktop": {"enabled": false}` in the config to turn it off. Wayland is not supported.

## MCP servers

Configure local stdio or remote Streamable HTTP servers in `~/.bluesilk/mcp.json`:

```json
{
  "mcpServers": {
    "local": {
      "command": "uv",
      "args": ["run", "/path/to/server.py"]
    },
    "remote": {
      "url": "https://example.com/mcp",
      "token": "bearer-token"
    }
  }
}
```

MCP tools are exposed as `<server>__<tool>`. bluesilk does not perform MCP OAuth, so provide credentials accepted by the server.

## Data and security

All state is stored as plain files under `~/.bluesilk/`, including credentials, conversations, memory, uploaded files, and job output.

bluesilk gives the agent unrestricted shell access as the account running it. Run it under a dedicated operating-system account, VM, or isolated machine when possible.

## Development

Run the offline test suite:

```sh
python -m unittest discover -s tests -v
```

Install the browser extra and run the suite with real Chromium integration tests:

```sh
uv sync --extra browser
uv run playwright install chromium
uv run python -m unittest discover -s tests -v
```

Build the package from the repository root:

```sh
uv build
```

## License

[MIT](LICENSE)
