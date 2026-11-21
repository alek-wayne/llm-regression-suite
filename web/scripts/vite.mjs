import { spawn } from "node:child_process";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const project = resolve(fileURLToPath(new URL("../", import.meta.url)));
const [command, ...flags] = process.argv.slice(2);
let scratch;
let root = project;
try {
  // Vite treats # and ? in source paths as URL suffixes. The directory
  // junction/symlink keeps the project and build output in their original place.
  if (/[#?*]/.test(project)) {
    scratch = await mkdtemp(join(tmpdir(), "eval-vite-"));
    root = join(scratch, "web");
    await symlink(project, root, process.platform === "win32" ? "junction" : "dir");
  }

  const vite = join(root, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, ["--preserve-symlinks", "--preserve-symlinks-main", vite, command, root, ...flags], {
    stdio: "inherit",
  });
  const stop = (signal) => child.kill(signal);
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  try {
    const result = await new Promise((complete, fail) => {
      child.once("error", fail);
      child.once("exit", (code, signal) => complete(code ?? (signal === "SIGINT" ? 130 : 143)));
    });
    process.exitCode = result;
  } finally {
    process.off("SIGINT", stop);
    process.off("SIGTERM", stop);
  }
} finally {
  if (scratch) await rm(scratch, { recursive: true, force: true });
}
