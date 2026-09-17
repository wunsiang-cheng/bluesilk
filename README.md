# bluesilk

A fast, minimal AI agent for individuals and trusted small teams. bluesilk runs on any tool-calling model on OpenRouter and is available through private Telegram chats, a built-in web console, or both.

## Features

- Separate conversations and work queues for each team member
- Shared memory, reusable skills, custom tools, and conversation history
- Built-in shell access, file transfer, image understanding, and background jobs
- Optional Chromium control through screenshots and mouse/keyboard actions
- Local and remote MCP server support
- Automatic context compaction and periodic memory maintenance

## Requirements

- Python 3.10 or newer on a POSIX system
- An [OpenRouter API key](https://openrouter.ai/keys)
- Optional: a Telegram bot token and member user IDs
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

You can run setup again from the terminal or browser:

```sh
bluesilk setup
bluesilk setup web
```

The setup requires at least one Telegram member or web-console member. Configuration and persistent data are stored in `~/.bluesilk/` by default. Set `BLUESILK_HOME` to use another directory.

### Model

Setup asks for an [OpenRouter model](https://openrouter.ai/models) slug; the default is `deepseek/deepseek-v4.1-flash`. The model must support tool calling. Context compaction is set to half of the model's context window. A text-only model still works, but photos and screenshots then reach the agent as file paths only, so browser and desktop control are of little use with it.

Upgrading from 0.6 or earlier: the stored key was for DeepSeek's own API. bluesilk runs setup again on the next start; enter an OpenRouter key.

## Interfaces

### Telegram

Create a bot with `@BotFather`. Before setup, each member must start the bot and provide their numeric Telegram user ID. Messages from unconfigured users and non-private chats are ignored.

### Web console

The web console listens on `http://127.0.0.1:8321/` by default and creates a private login link for each member.

- `Esc` stops the current response.
- `Ctrl+U`, paste, or drag and drop sends a file.
- `/new` starts a new personal conversation.
- `/reset` resets shared agent data and creates a backup.
- `/settings` opens configuration.
- `/theme` switches the color theme.

The console uses plain HTTP. Keep it on localhost or a trusted private network, or place it behind a TLS reverse proxy.

## Browser control

Install the optional browser support and Chromium:

```sh
uv tool install 'bluesilk[browser]'
bluesilk browser install
```

This adds a visual `computer` tool. Each member gets an isolated browser context with separate cookies, tabs, and downloads. Headless mode is enabled by default; browser visibility, cursor display, and action delay can be changed in `/settings`.

## Desktop control

On an X11 session with `xdotool` and `scrot` (or `ffmpeg`) installed, bluesilk also gets a `desktop` tool that acts on the real screen from screenshots:

```sh
sudo apt install xdotool scrot
```

The screen is one physical desktop shared by the whole team, so actions are serialised and the agent is told to look before it acts. Set `"desktop": {"enabled": false}` in the config to turn it off. Wayland is not supported.

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

bluesilk gives the agent unrestricted shell access as the account running it. Treat every configured member as fully trusted and run it under a dedicated operating-system account, VM, or isolated machine when possible.

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
