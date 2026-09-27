# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability** (private vulnerability reporting). Do not open a public issue for security problems.

Include what you found, how to reproduce it, and the version or commit you tested. You will get an answer as soon
as possible; fixes are released on the `main` branch.

## Scope and threat model

MCP-ADAPTER lets an AI agent drive engineering applications that are installed on the same computer. By design,
several tools run code in those applications with the rights of the user who started the server (see
"What an MCP client can do with this server" in the README). The security goals are therefore:

* **Only the owner can drive the server.** stdio opens no socket. Every HTTP transport requires a bearer token
  (`MCP_ADAPTER_AUTH_TOKEN`), also in the default `local` mode, which additionally binds loopback only and checks
  `Host`/`Origin` headers; `network` mode must be chosen explicitly.
* **No internet access unless enabled** (`MCP_ADAPTER_ALLOW_INTERNET=false` by default removes the web tools).
* **Secrets stay local and out of reach of the tools**: API keys and tokens live in `.env` (never committed,
  readable only by the owner, write-only in the UI). They are kept out of the environment of the MCP server's
  child processes, tool paths do not expand environment variables, and transcripts redact key values.
* **The workspace UI is private to the local user**: loopback binding, a per-session key delivered only through
  the launch link, exact same-origin checks, no CORS, a nonce-based content security policy, and approvals bound
  to the specific tool call.
* **Untrusted design files are parsed safely**: bounded decompression and archive reads, no external XML entities,
  and generated scripts escape names and paths.

Reports about a way around any of these goals are in scope. That a connected, trusted MCP client can run code
through the documented code-execution tools is expected behaviour, not a vulnerability.
