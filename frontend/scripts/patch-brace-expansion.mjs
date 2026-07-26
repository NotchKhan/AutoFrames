import fs from "node:fs";
import path from "node:path";

const nodeModules = path.resolve(import.meta.dirname, "..", "node_modules");
const originalLine = "var expand = require('brace-expansion')";
const patchedLines = [
  "var braceExpansion = require('brace-expansion')",
  "var expand = braceExpansion.expand || braceExpansion",
].join("\n");

let patchedCount = 0;

function patchLegacyMinimatch(directory) {
  const manifestPath = path.join(directory, "package.json");
  if (!fs.existsSync(manifestPath)) return false;
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (manifest.name !== "minimatch") return false;
  const major = Number.parseInt(String(manifest.version).split(".")[0], 10);
  if (!Number.isFinite(major) || major >= 10) return true;

  const sourcePath = path.join(directory, "minimatch.js");
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`Не найден ожидаемый файл совместимости: ${sourcePath}`);
  }
  const source = fs.readFileSync(sourcePath, "utf8");
  if (source.includes(patchedLines)) return true;
  if (!source.includes(originalLine)) {
    throw new Error(`Не найдена ожидаемая строка brace-expansion в ${sourcePath}`);
  }
  fs.writeFileSync(sourcePath, source.replace(originalLine, patchedLines), "utf8");
  patchedCount += 1;
  return true;
}

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.isSymbolicLink() || entry.name === ".bin") continue;
    const child = path.join(directory, entry.name);
    if (!patchLegacyMinimatch(child)) walk(child);
  }
}

if (!fs.existsSync(nodeModules)) {
  throw new Error("Каталог node_modules отсутствует: сначала выполните npm install.");
}

walk(nodeModules);
console.log(`Совместимость безопасного brace-expansion применена: ${patchedCount} пакетов.`);
