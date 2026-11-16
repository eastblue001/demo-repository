# Validation report

Validated on 2026-11-15 in the build workspace.

## Passed

- Python syntax compilation for `src/` and `tests/`
- 6 unit tests with Yahoo Finance network calls replaced by deterministic test doubles
- Registration of all 7 MCP tools
- Streamable HTTP startup configuration (`0.0.0.0:8000/mcp`, stateless mode)
- `pyproject.toml` parsing and dependency pin checks
- Bash syntax check for `deploy/deploy.sh`
- Source line-length scan (100 characters)

## Environment limitations

Docker/Podman, Azure CLI/Bicep, and outbound Python package installation were not available in the validation workspace. Therefore an actual image build, real Yahoo Finance network call, and Azure deployment were not executed here. The Dockerfile, Bicep, and deployment script are included for execution in an environment with Docker/Azure CLI and internet access.
