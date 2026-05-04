MCP Service Library

This library provides a small boilerplate to create and run Model Context Protocol (MCP) servers in our services.

Testing with MCP Inspector

Run the MCP Inspector locally to test your server:

```bash
docker run --rm \
  -e HOST=0.0.0.0 \
  -e ALLOWED_ORIGINS="http://localhost:6274,http://127.0.0.1:6274" \
  -e DANGEROUSLY_OMIT_AUTH=true \
  -p 6274:6274 -p 6277:6277 \
  ghcr.io/modelcontextprotocol/inspector:latest
```


Authentication (Bearer token / API key)
--------------------------------------

Enable simple Bearer token (API key) auth for HTTP transports (`streamable-http` and `sse`).

Config options (env vars are prefixed with `APP_`):

- `mcp_auth_enabled` (bool): enable/disable auth. Default: false
- `mcp_auth_api_key` (str): allowed API key.

Example `.env` entries:

```env
APP_MCP_AUTH_ENABLED=true
APP_MCP_AUTH_API_KEY="dev-secret-key-1"
# No scopes or issuer/resource URLs are required; these are derived automatically
```
