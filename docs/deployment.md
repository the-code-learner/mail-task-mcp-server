# Single-YAML deployment

`postmaster-mcp.yml` is the only file that must be pasted into Portainer. It bootstraps the application source from the public GitHub repository into a persistent code volume, prepares a versioned Python virtual environment, prepares the compact context model when needed, and starts the MCP/WebGUI service.

For production, pin `NOMADCOMPASS_REF` to an immutable tag or commit SHA. A mutable branch is useful only while testing upgrades.

Persistent state lives in `/data`; source code and the venv are disposable/rebuildable. Updating the code does not overwrite SQLite state or model data.
