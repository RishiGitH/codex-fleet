#!/usr/bin/env node
const { spawnSync } = require('child_process');
const { existsSync, mkdirSync } = require('fs');
const { join, resolve } = require('path');

const root = resolve(__dirname, '..');
const projectCwd = resolve(process.env.CODEX_FLEET_PROJECT_CWD || process.env.INIT_CWD || process.cwd());
const localDev = projectCwd === root;
const venv = localDev
  ? join(root, '.venv')
  : join(projectCwd, '.codex-fleet', 'tooling', 'codex-fleet-venv');
const python = process.platform === 'win32'
  ? join(venv, 'Scripts', 'python.exe')
  : join(venv, 'bin', 'python');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: 'inherit',
    shell: false,
    ...options,
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function canImportCodexFleet() {
  const result = spawnSync(python, ['-c', 'import codex_fleet'], {
    cwd: root,
    stdio: 'ignore',
    shell: false,
  });
  return !result.error && result.status === 0;
}

function findSystemPython() {
  const candidates = process.env.PYTHON
    ? [process.env.PYTHON]
    : process.platform === 'win32'
      ? ['py', 'python']
      : ['python3', 'python'];
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ['--version'], { stdio: 'ignore', shell: false });
    if (!result.error && result.status === 0) return candidate;
  }
  console.error('Python 3 is required to create the codex-fleet tool environment.');
  process.exit(1);
}

if (!existsSync(python)) {
  mkdirSync(venv, { recursive: true });
  run(findSystemPython(), ['-m', 'venv', venv]);
}

if (!canImportCodexFleet()) {
  run(python, ['-m', 'pip', 'install', '-e', '.']);
}
run(python, ['-m', 'codex_fleet', ...process.argv.slice(2)], { cwd: projectCwd });
