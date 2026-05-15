import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const dashboardDir = path.join(root, 'apps', 'dashboard');
const pythonPath = path.join(root, 'libs', 'agentx-core');
const isWindows = process.platform === 'win32';

function loadDotEnv() {
  // Minimal .env parser (keeps this launcher dependency-free).
  const envPath = path.join(root, '.env');
  if (!existsSync(envPath)) return {};

  const out = {};
  const raw = readFileSync(envPath, 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if (!key) continue;

    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    // Do not overwrite environment already set by the shell.
    if (process.env[key] === undefined) out[key] = value;
  }
  return out;
}

function commandExists(command, args = ['--version']) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: 'ignore',
    shell: isWindows,
  });
  return result.status === 0;
}

function resolvePython() {
  const explicit = process.env.AJA_PYTHON || process.env.PYTHON || '';
  if (explicit) {
    const resolved = path.isAbsolute(explicit) ? explicit : path.join(root, explicit);
    if (existsSync(resolved)) {
      return { command: `"${resolved}"`, args: [] };
    }
  }

  // Common Windows install locations (helps when PATH points to WindowsApps shim).
  if (isWindows) {
    const home = process.env.USERPROFILE || '';
    const candidates = [
      'C:\\Python313\\python.exe',
      'C:\\Python312\\python.exe',
      'C:\\Python311\\python.exe',
      home ? path.join(home, 'AppData', 'Local', 'Programs', 'Python', 'Python313', 'python.exe') : '',
      home ? path.join(home, 'AppData', 'Local', 'Programs', 'Python', 'Python312', 'python.exe') : '',
      home ? path.join(home, 'AppData', 'Local', 'Programs', 'Python', 'Python311', 'python.exe') : '',
    ].filter(Boolean);

    for (const exe of candidates) {
      if (existsSync(exe)) return { command: `"${exe}"`, args: [] };
    }
  }

  const candidates = isWindows
    ? [
      { command: 'py', args: ['-3.13'] },
      { command: 'py', args: ['-3.12'] },
      { command: 'py', args: ['-3'] },
      { command: 'python', args: [] },
    ]
    : [
      { command: 'python3.13', args: [] },
      { command: 'python3', args: [] },
      { command: 'python', args: [] },
    ];

  for (const candidate of candidates) {
    if (commandExists(candidate.command, [...candidate.args, '--version'])) {
      return candidate;
    }
  }
  return null;
}

function spawnService(name, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd || root,
    env: options.env || process.env,
    stdio: 'inherit',
    shell: isWindows,
  });

  child.on('exit', (code, signal) => {
    if (!shuttingDown && code !== 0) {
      console.error(`\n[AJA] ${name} stopped unexpectedly (${signal || code}).`);
    }
  });

  return child;
}

function hasAnyEnvFrom(env, names) {
  return names.some((name) => Boolean(env[name]));
}

function printHeader() {
  console.log('\n        AJA (Assistant of Joint Agents)');
  console.log('        Starting API bridge, dashboard, and Telegram gateway.\n');
}

function printReadiness() {
  const modelReady = hasAnyEnvFrom(process.env, [
    'GEMINI_API_KEY',
    'GOOGLE_API_KEY',
    'OPENAI_API_KEY',
    'OPENROUTER_API_KEY',
    'AI_KEY',
  ]);
  const telegramReady = Boolean((process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_TOKEN) && process.env.TELEGRAM_ALLOWED_USER_ID);

  console.log('[AJA] Dashboard: http://localhost:5173');
  console.log('[AJA] API bridge: http://localhost:8000');
  console.log(
    `[AJA] Model key: ${modelReady ? 'configured' : 'missing (set GEMINI_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY or run local llama.cpp)'}`
  );
  console.log(
    `[AJA] Telegram: ${telegramReady ? 'configured' : 'missing (set TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USER_ID)'}`
  );
  console.log('\nPress Ctrl+C to stop everything.\n');
}

let shuttingDown = false;
const children = [];

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log('\n[AJA] Stopping services...');
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  setTimeout(() => process.exit(0), 500);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

printHeader();

if (!existsSync(dashboardDir)) {
  console.error(`[AJA] Dashboard directory not found: ${dashboardDir}`);
  process.exit(1);
}

const dotenvVars = loadDotEnv();

const python = resolvePython();
if (!python) {
  console.error('[AJA] Python was not found. Install Python 3.13+ or fix the Windows Python launcher, then run this again.');
  console.error('[AJA] The dashboard can run with npm, but AJA/Telegram need the Python API bridge.');
  process.exit(1);
}
console.log(`[AJA] Using Python: ${python.command} ${python.args.join(' ')}`);

const env = {
  ...process.env,
  ...dotenvVars,
  PYTHONPATH: process.env.PYTHONPATH ? `${pythonPath}${path.delimiter}${process.env.PYTHONPATH}` : pythonPath,
  OPENBLAS_NUM_THREADS: '1',
  OPENBLAS_MAIN_FREE: '1',
  KMP_DUPLICATE_LIB_OK: 'TRUE',
  OMP_NUM_THREADS: '1',
  MKL_NUM_THREADS: '1',
  PYTHONUNBUFFERED: '1',
  PYTHONIOENCODING: 'utf-8',
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Print readiness using the effective env we will pass to child processes.
process.env = env;

console.log('[AJA] Launching Hardened v2.0 Swarm...');

// 1. Launch the Unified Gateway (Telegram + Telemetry Bridge)
children.push(
  spawnService('Unified Gateway', python.command, [...python.args, '-m', 'agentx.gateway.server'], { env }),
);

await sleep(5000);

// 2. Launch the Autonomous Worker (The Terminal Engine)
children.push(
  spawnService('Autonomous Worker', python.command, [...python.args, '-m', 'agentx.runtime.autonomous_loop'], { env }),
);

await sleep(5000);

// 3. Launch the Dashboard
children.push(
  spawnService('Dashboard', 'npm', ['run', 'dev', '-w', '@agent/dashboard'], { env }),
);

printReadiness();
