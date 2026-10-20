import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const helperSourcePath = fileURLToPath(new URL("./openwoof-keychain.swift", import.meta.url));
const helperSource = await readFile(helperSourcePath, "utf8");

function checkStaticBoundaries() {
  assert.match(helperSource, /NSAppleScript\(source:/);
  assert.match(helperSource, /with hidden answer/);
  assert.doesNotMatch(helperSource, /\/usr\/bin\/osascript|\bProcess\s*\(/);
  assert.doesNotMatch(helperSource, /ProcessInfo\.processInfo\.environment|\bgetenv\s*\(/);
  assert.doesNotMatch(helperSource, /\bSecItem(?:Update|Delete)\b/);
  assert.match(helperSource, /kSecAttrService as String: ingestService/);
  assert.match(helperSource, /kSecAttrAccount as String: ingestAccount/);
  assert.match(helperSource, /"openwoof\/openwoof\.javadevjt\.tech\/truenas-192-168-0-2\/imap-sync\/production"/);
  assert.match(helperSource, /"codex-truenas-mcp"/);
  assert.match(helperSource, /"joshuaterk"/);
  assert.match(helperSource, /writeAll\(secret, to: secretFileDescriptor\)/);

  for (const mode of ["read-nas-to-fd", "read-ingest-to-fd", "prompt-ingest-to-fd", "--test-ipc"]) {
    const escapedMode = mode.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(helperSource, new RegExp(`case "${escapedMode}":\\s*try requirePrivatePipe\\(\\)`));
  }

  const statusStart = helperSource.indexOf("private static func statusIngest()")
  const statusEnd = helperSource.indexOf("private static func requirePrivatePipe()", statusStart);
  assert.notEqual(statusStart, -1);
  assert.notEqual(statusEnd, -1);
  const statusBody = helperSource.slice(statusStart, statusEnd);
  assert.match(statusBody, /includeData: false/);
  assert.doesNotMatch(statusBody, /kSecReturnData/);

  const addStart = helperSource.indexOf("private static func addNewIngestKey(");
  const addEnd = helperSource.indexOf("private static func validatedSecret(", addStart);
  assert.notEqual(addStart, -1);
  assert.notEqual(addEnd, -1);
  const addBody = helperSource.slice(addStart, addEnd);
  assert.match(addBody, /SecItemAdd/);
  assert.match(addBody, /errSecDuplicateItem[\s\S]*keychain-entry-raced/);
}

function runHelper(binaryPath, args, { pipeFD3 = true } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(binaryPath, args, {
      stdio: pipeFD3 ? ["ignore", "pipe", "pipe", "pipe"] : ["ignore", "pipe", "pipe", "ignore"],
    });
    const stdout = [];
    const stderr = [];
    const secretPipe = [];

    child.stdout.on("data", chunk => stdout.push(chunk));
    child.stderr.on("data", chunk => stderr.push(chunk));
    if (pipeFD3) child.stdio[3].on("data", chunk => secretPipe.push(chunk));
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({
      code,
      signal,
      stdout: Buffer.concat(stdout).toString("utf8"),
      stderr: Buffer.concat(stderr).toString("utf8"),
      secretPipe: Buffer.concat(secretPipe),
    }));
  });
}

async function main() {
  assert.equal(process.platform, "darwin", "the Security.framework helper test requires macOS");
  checkStaticBoundaries();

  const temporaryDirectory = await mkdtemp("/private/tmp/openwoof-keychain-helper-test-");
  try {
    const binaryPath = path.join(temporaryDirectory, "openwoof-keychain-helper");
    const moduleCachePath = path.join(temporaryDirectory, "module-cache");
    await mkdir(moduleCachePath, { recursive: true });
    const compile = spawnSync("swiftc", ["-module-cache-path", moduleCachePath, "-o", binaryPath, helperSourcePath], { encoding: "utf8" });
    assert.equal(compile.status, 0, `swiftc failed: ${compile.stderr || compile.stdout}`);

    const selfTest = await runHelper(binaryPath, ["--self-test"]);
    assert.equal(selfTest.code, 0, selfTest.stderr);
    assert.equal(selfTest.stderr, "");
    const selfTestStatus = JSON.parse(selfTest.stdout);
    assert.equal(selfTestStatus.ok, true);
    assert.equal(selfTestStatus.mode, "self-test");
    assert.equal(selfTestStatus.checks, 8);
    assert.equal(selfTest.stdout.includes("synthetic-self-test-token"), false);
    assert.equal(selfTest.secretPipe.length, 0);

    const ipc = await runHelper(binaryPath, ["--test-ipc"]);
    assert.equal(ipc.code, 0, ipc.stderr);
    assert.equal(ipc.stderr, "");
    assert.deepEqual(ipc.secretPipe, Buffer.from("synthetic-ipc-token-7b9e2c41"));
    assert.equal(ipc.stdout.includes("synthetic-ipc-token-7b9e2c41"), false);
    assert.equal(ipc.stderr.includes("synthetic-ipc-token-7b9e2c41"), false);
    const ipcStatus = JSON.parse(ipc.stdout);
    assert.equal(ipcStatus.ok, true);
    assert.equal(ipcStatus.mode, "test-ipc");

    const withoutPipe = await runHelper(binaryPath, ["--test-ipc"], { pipeFD3: false });
    assert.notEqual(withoutPipe.code, 0);
    assert.equal(withoutPipe.stdout, "");
    assert.match(withoutPipe.stderr, /fd3-private-pipe-required/);
    assert.equal(withoutPipe.stderr.includes("synthetic-ipc-token-7b9e2c41"), false);

    process.stdout.write("keychain helper tests passed: static boundaries, validation, fd3 capture, and stdout non-disclosure\n");
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await main();
