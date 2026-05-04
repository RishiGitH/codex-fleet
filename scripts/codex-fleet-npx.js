#!/usr/bin/env node
const { spawnSync } = require('child_process');
const { existsSync } = require('fs');
const { join, resolve } = require('path');

const root = resolve(__dirname, '..');
const venv = join(root, '.venv');
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
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

if (!existsSync(python)) {
  run('python', ['-m', 'venv', '.venv']);
}

run(python, ['-m', 'pip', 'install', '-e', '.']);
run(python, ['-m', 'codex_fleet', ...process.argv.slice(2)]);
