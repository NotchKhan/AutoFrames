const rawApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim() ?? "";
const argumentsSet = new Set(process.argv.slice(2));
const isVercelBuild = process.env.VERCEL === "1" || Boolean(process.env.VERCEL_ENV);
const configurationIsRequired =
  argumentsSet.has("--require-api-url") ||
  isVercelBuild ||
  process.env.REQUIRE_API_URL === "true";
const httpsIsRequired =
  argumentsSet.has("--require-https") ||
  isVercelBuild ||
  process.env.REQUIRE_HTTPS_API_URL === "true";
const loopbackIsAllowed = process.env.ALLOW_LOOPBACK_API_URL === "true";

function fail(message) {
  console.error(`\n[AutoFrames] ${message}\n`);
  process.exit(1);
}

if (!rawApiUrl) {
  if (configurationIsRequired) {
    fail(
      "NEXT_PUBLIC_API_URL не задан. Сначала опубликуйте backend, укажите его публичный HTTPS URL " +
        "в переменных окружения frontend и запустите deployment заново.",
    );
  }

  console.log(
    "[AutoFrames] NEXT_PUBLIC_API_URL не задан: локальный frontend будет обращаться к backend на порту 8000.",
  );
  process.exit(0);
}

let parsed;
try {
  parsed = new URL(rawApiUrl);
} catch {
  fail("NEXT_PUBLIC_API_URL должен быть абсолютным URL, например https://api.example.com.");
}

if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
  fail("NEXT_PUBLIC_API_URL должен использовать протокол http:// или https://.");
}

if (parsed.username || parsed.password || parsed.search || parsed.hash) {
  fail("NEXT_PUBLIC_API_URL не должен содержать логин, пароль, query-параметры или fragment.");
}

const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
if (configurationIsRequired && !loopbackIsAllowed && loopbackHosts.has(parsed.hostname)) {
  fail("Для облачного deployment NEXT_PUBLIC_API_URL не может указывать на localhost.");
}

if (httpsIsRequired && parsed.protocol !== "https:") {
  fail("Deployment на Vercel требует HTTPS backend в NEXT_PUBLIC_API_URL.");
}

console.log(`[AutoFrames] Backend для frontend-сборки: ${parsed.origin}${parsed.pathname.replace(/\/$/, "")}`);
