# winphone

Drive real Android phones (and read iPhones) from Windows: a CLI, a background service and an MCP
server for Claude Code, in one program.

```bash
npm install -g winphone
winphone setup      # pinned adb, scrcpy and go-ios, SHA-256 checked
winphone doctor
winphone mcp install   # register with Claude Code
```

Or let Claude Code start it on demand (on Windows, `npx` needs `cmd /c`):
`claude mcp add --scope user winphone -- cmd /c npx -y winphone mcp`.

Windows 10/11 x64 only. On install, the package downloads `winphone.exe` for its version and refuses it
unless the SHA-256 matches the one pinned in `package.json`.
