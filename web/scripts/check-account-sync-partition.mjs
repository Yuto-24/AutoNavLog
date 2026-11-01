import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const projects = ['chromium', 'ipad-webkit', 'iphone-webkit'];

export function identities(report) {
  assert.deepEqual(report.errors ?? [], [], 'Playwright collection errors');
  const found = [];
  const visit = suite => {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests) {
        found.push(JSON.stringify([spec.file, spec.line, spec.column, spec.title, test.projectName]));
      }
    }
    for (const child of suite.suites ?? []) visit(child);
  };
  for (const suite of report.suites) visit(suite);
  assert.ok(found.length, 'empty collection');
  return found.sort();
}

export function verify(full, shards) {
  assert.deepEqual(shards.flat().sort(), full, 'project partition omitted or duplicated tests');
}

function collect(config, project) {
  const args = [resolve(web, 'node_modules/@playwright/test/cli.js'), 'test',
    '--config', `playwright.${config}.config.ts`, '--list', '--reporter=json'];
  if (project) args.push('--project', project);
  return identities(JSON.parse(execFileSync(process.execPath, args,
    { cwd: web, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })));
}

export function check() {
  const workflow = readFileSync(resolve(web, '../.github/workflows/test.yml'), 'utf8');
  const block = workflow.split('  account-sync-tests:\n')[1]?.split('\n  account-sync:\n')[0];
  const matrix = block?.match(/suite: \[([^\]]+)\]/)?.[1].split(',').map(value => value.trim());
  assert.deepEqual(matrix, [...projects, 'contracts'], 'workflow matrix must cover every suite');
  const full = collect('sync');
  verify(full, projects.map(project => collect('sync', project)));
  const contracts = Object.fromEntries(['firestore', 'storage-upgrade', 'migration']
    .map(config => [config, collect(config).length]));
  console.log(JSON.stringify({ sync: full.length, projects, contracts }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) check();
