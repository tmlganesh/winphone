// Fetches winphone.exe (a zipped folder) for this package version, checks its SHA-256 against
// package.json, and unpacks it next to this file. WINPHONE_DIST=<path to winphone-win64.zip> installs
// from a local build instead of downloading (used before a release exists).
"use strict";
const crypto = require("crypto");
const fs = require("fs");
const https = require("https");
const path = require("path");
const { execFileSync } = require("child_process");

const pkg = require("./package.json");
const target = path.join(__dirname, "dist");

function download(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { "User-Agent": "winphone-npm" } }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects > 0) {
        res.resume();
        return resolve(download(new URL(res.headers.location, url).toString(), redirects - 1));
      }
      if (res.statusCode !== 200) return reject(new Error(`download failed: HTTP ${res.statusCode}`));
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

async function main() {
  if (process.platform !== "win32") throw new Error("winphone runs on Windows only");
  const expected = pkg.winphone.sha256;
  if (!expected) throw new Error("this package has no pinned SHA-256 (build it with packaging/build.py)");
  let zip;
  if (process.env.WINPHONE_DIST) {
    zip = fs.readFileSync(process.env.WINPHONE_DIST);
  } else if (pkg.winphone.url) {
    zip = await download(pkg.winphone.url);
  } else {
    throw new Error("no release has been published yet: set WINPHONE_DIST to a local winphone-win64.zip");
  }
  const actual = crypto.createHash("sha256").update(zip).digest("hex");
  if (actual !== expected) throw new Error(`checksum mismatch: expected ${expected}, got ${actual}; nothing installed`);
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(target, { recursive: true });
  const tmp = path.join(target, "winphone-win64.zip");
  fs.writeFileSync(tmp, zip);
  // Windows 10+ ships bsdtar as System32\tar.exe, which reads zip files: no npm dependencies. Called by full
  // path: a bare "tar" could find Git's GNU tar (cannot read zips) or a tar.exe in the working folder.
  const tar = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe");
  execFileSync(tar, ["-xf", tmp, "-C", target], { stdio: "inherit" });
  fs.rmSync(tmp);
  console.log(`winphone ${pkg.winphone.version} installed (sha256 ${actual.slice(0, 12)}...)`);
}

main().catch((e) => {
  console.error(`winphone install failed: ${e.message}`);
  process.exit(1);
});
