#!/usr/bin/env node
// Runs the bundled winphone.exe with the same arguments and stdio (so `npx -y winphone mcp` speaks
// MCP over this process's stdin/stdout), and exits with its exit code.
"use strict";
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");

const exe = path.join(__dirname, "..", "dist", "winphone", "winphone.exe");
if (!fs.existsSync(exe)) {
  console.error("winphone.exe is missing: reinstall the package (npm rebuild winphone).");
  process.exit(1);
}
const child = spawn(exe, process.argv.slice(2), { stdio: "inherit", windowsHide: false });
child.on("exit", (code, signal) => process.exit(code === null ? 1 : code));
child.on("error", (e) => {
  console.error(`could not start winphone.exe: ${e.message}`);
  process.exit(1);
});
