// Run with Node's built-in test runner; no third-party DOM package is needed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page() {
  class Element {
    constructor() { this.children = []; this.dataset = {}; this.value = 'all'; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute() {}
  }
  const elements = new Map();
  const requests = new Map();
  const failures = new Map();
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
      },
      createElement() { return new Element(); },
    },
    fetch(url) {
      return new Promise((resolve, reject) => {
        requests.set(url, value => resolve({ ok: true, json: async () => value }));
        failures.set(url, reject);
      });
    },
    setInterval() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), context);
  vm.runInContext('state = { cases: [], analyses: [] };', context);
  function call(code) { return vm.runInContext(code, context); }
  function reply(url, value) {
    assert.ok(requests.has(url), `request exists: ${url}`);
    requests.get(url)(value);
    requests.delete(url);
  }
  function factText() {
    function text(node) {
      return [node.textContent || '', ...node.children.map(text)].join(' ');
    }
    return text(elements.get('facts'));
  }
  return { call, reply, factText, elements, reject(url) { failures.get(url)(new Error("fixture request failure")); } };
}

function row(id, label) {
  return {
    id, scenario: 'sqlite_lock', state: 'complete', captured: 1,
    evidence: { facts: [{ id: 'L04', kind: 'log', text: label }] },
    raw: { outcomes: [], telemetry: { records: [] } },
  };
}

async function select(p, id, value) {
  const pending = p.call(`selectCase(${JSON.stringify(id)})`);
  p.reply(`/api/cases/${id}`, value);
  await pending;
}

test('failed case clears prior facts during loading and after the empty response', async () => {
  const p = page();
  await select(p, 'first', row('first', 'FIRST_CASE_ONLY'));
  assert.match(p.factText(), /FIRST_CASE_ONLY/);
  const pending = p.call('selectCase("failed")');
  assert.doesNotMatch(p.factText(), /FIRST_CASE_ONLY/);
  p.reply('/api/cases/failed', {
    id: 'failed', scenario: 'slow_aggregate', state: 'failed', captured: 2,
    evidence: null, raw: null, error: 'timed out',
  });
  await pending;
  assert.equal(p.factText().trim(), 'No evidence loaded for this selection.');
  p.call('renderFacts()'); // A filter change must not revive stale facts.
  assert.doesNotMatch(p.factText(), /FIRST_CASE_ONLY/);
});

test('late case and analysis responses cannot replace the current selection', async () => {
  const p = page();
  const old = p.call('selectCase("old")');
  const current = p.call('selectCase("current")');
  p.reply('/api/cases/current', row('current', 'CURRENT_CASE_ONLY'));
  await current;
  p.reply('/api/cases/old', row('old', 'OLD_CASE_ONLY'));
  await old;
  assert.match(p.factText(), /CURRENT_CASE_ONLY/);
  assert.doesNotMatch(p.factText(), /OLD_CASE_ONLY/);

  const analysis = p.call('selectAnalysis("old-analysis")');
  assert.doesNotMatch(p.factText(), /CURRENT_CASE_ONLY/);
  await select(p, 'latest', row('latest', 'LATEST_CASE_ONLY'));
  p.reply('/api/analyses/old-analysis', {
    id: 'old-analysis', case_id: 'current', bundle: row('current', 'OLD_ANALYSIS_ONLY').evidence,
  });
  await analysis;
  assert.match(p.factText(), /LATEST_CASE_ONLY/);
  assert.doesNotMatch(p.factText(), /OLD_ANALYSIS_ONLY|CURRENT_CASE_ONLY/);
});


test('a rejected case load leaves no prior requests, raw data, metrics or export link', async () => {
  const p = page();
  await select(p, 'first', row('first', 'FIRST_CASE_ONLY'));
  p.elements.get('raw').textContent = 'OLD_RAW';
  p.elements.get('base-errors').textContent = '3 / 3';
  p.elements.get('spans').append({ children: [], textContent: 'OLD_SPAN' });
  const pending = p.call('selectCase("unavailable")');
  assert.equal(p.elements.get('raw').textContent, '');
  assert.equal(p.elements.get('base-errors').textContent, '—');
  assert.equal(p.elements.get('fault-errors').textContent, '—');
  assert.equal(p.elements.get('latency').textContent, '—');
  assert.equal(p.elements.get('export').hidden, true);
  assert.equal(p.elements.get('export').href, '');
  assert.equal(p.elements.get('spans').children.length, 0);
  assert.equal(p.elements.get('requests').children[0].textContent, 'Loading selected case…');
  assert.doesNotMatch(p.factText(), /FIRST_CASE_ONLY/);
  p.reject('/api/cases/unavailable');
  await assert.rejects(pending, /fixture request failure/);
  assert.equal(p.elements.get('raw').textContent, '');
  assert.equal(p.elements.get('export').hidden, true);
  assert.doesNotMatch(p.factText(), /FIRST_CASE_ONLY/);
});
