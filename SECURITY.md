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

* **Nothing outside the computer can reach the server unless the owner explicitly allows it.** The default
  `local` mode binds loopback only and checks `Host`/`Origin` headers; `network` mode requires a bearer token
  (`MCP_ADAPTER_AUTH_TOKEN`) on every HTTP request.
* **No internet access unless enabled** (`MCP_ADAPTER_ALLOW_INTERNET=false` by default removes the web tools).
* **Secrets stay local**: API keys and tokens live in `.env`, which is never committed and is write-only in the UI.
* **The workspace UI is private to the local user**: loopback binding, a per-session token on every API call,
  same-origin checks and no CORS.

Reports about a way around any of these goals are in scope. That a connected, trusted MCP client can run code
through the documented code-execution tools is expected behaviour, not a vulnerability.
