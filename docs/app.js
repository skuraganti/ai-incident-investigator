const $ = id => document.getElementById(id), el = (tag, text = '', parent) => { const node = document.createElement(tag); node.textContent = text; parent.append(node); return node; }, clear = node => node.replaceChildren();
const controls = $('controls'), workspace = $('workspace');
let data, caseRecord;
function panel(title, id, wide = false) { const section = el('section', '', workspace); section.className = `panel${wide ? ' wide' : ''}`; const h = el('h2', title, section); h.id = `${id}-title`; section.setAttribute('aria-labelledby', h.id); return section; }
function button(text, id, action) { const b = el('button', text, controls); b.id = id; b.type = 'button'; b.addEventListener('click', action); return b; }
function setup() { controls.className = 'controls'; let label = el('label', 'Recorded case', controls), select = el('select', '', label); select.id = 'case'; label = el('label', 'Evidence signal', controls); const signal = el('select', '', label); signal.id = 'signal'; [['All facts', 'all'], ['HTTP', 'http'], ['Logs', 'log'], ['Spans', 'trace'], ['Metrics', 'metric'], ['Dataset', 'dataset']].forEach(([text, value]) => signal.add(new Option(text, value))); label = el('label', 'Saved diagnosis', controls); const policy = el('select', '', label); policy.id = 'policy'; policy.add(new Option('Deterministic baseline', 'baseline')); policy.add(new Option('Recorded model attempt', 'model')); button('Inspect saved result', 'investigate', investigate); button('Export case JSON', 'export', exportCase).className = 'secondary'; const summary = panel('Study readout · recorded conditions', 'study', true); el('strong', '2 / 7 validated', summary).className = 'metric'; el('p', 'Only two of seven frozen study conditions produced validated diagnoses. These local requests are correlated, not production samples.', summary); el('p', '', summary).id = 'case-meta'; const requests = panel('Recorded requests · inspect traces', 'requests'); el('div', '', requests).id = 'request-list'; el('div', 'Select a request to inspect matching spans and log.', requests).id = 'request-details'; const facts = panel('Bounded observed evidence', 'facts'); const factRegion = el('div', '', facts); factRegion.id = 'facts'; factRegion.className = 'scroll'; factRegion.setAttribute('role', 'region'); factRegion.setAttribute('aria-label', 'Evidence facts'); const diagnosis = panel('Saved validated / failed diagnosis', 'diagnosis', true); const diag = el('div', 'Choose a case and inspect the saved result.', diagnosis); diag.id = 'diagnosis'; diag.setAttribute('role', 'region'); diag.setAttribute('aria-label', 'Saved diagnosis'); diag.setAttribute('aria-live', 'polite'); const repair = panel('Human-review plan only', 'repair', true); el('div', 'No plan displayed. Nothing can be approved or applied here.', repair).id = 'repair'; select.addEventListener('change', selectCase); signal.addEventListener('change', renderFacts); policy.addEventListener('change', () => { clear($('diagnosis')); $('diagnosis').textContent = 'Policy changed. Inspect the selected saved result.'; clear($('repair')); $('repair').textContent = 'No plan displayed.'; }); }
function selectCase() { caseRecord = data.cases.find(c => c.id === $('case').value); if (!caseRecord)
    return; const c = caseRecord; $('case-meta').textContent = `${c.label} · ${c.id === 'normal' ? 'healthy control' : 'explicitly injected cause'} · captured ${c.captured_at} · frozen USGS source SHA-256 ${c.evidence.source_sha256} · 3 healthy and 3 experiment requests.`; clear($('diagnosis')); $('diagnosis').textContent = 'Select Inspect saved result; no new inference occurs.'; clear($('repair')); $('repair').textContent = 'No plan displayed. Nothing can be applied.'; renderRequests(); renderFacts(); }
function renderRequests() { const root = $('request-list'); clear(root); caseRecord.outcomes.forEach((request, index) => { const p = el('p', '', root), b = el('button', `${index + 1}. ${request.phase} · HTTP ${request.status} · ${request.client_ms} ms`, p); b.type = 'button'; b.className = 'secondary'; b.addEventListener('click', () => showRequest(request)); }); $('request-details').textContent = 'Choose a request to see matching log and spans.'; }
function showRequest(request) { const root = $('request-details'); clear(root); const trace = caseRecord.telemetry.find(t => t.trace_id === request.trace_id); el('h3', `${request.phase} · trace ${request.trace_id}`, root); el('p', `Observed HTTP ${request.status} · client ${request.client_ms} ms · server ${trace?.duration_ms ?? 'unavailable'} ms`, root); if (!trace) {
    el('p', 'No matching telemetry recorded.', root);
    return;
} el('p', `Log: ${trace.log?.severity || 'unavailable'} · ${trace.log?.message || 'unavailable'}`, root); const table = el('table', '', root), cap = el('caption', 'Recorded matching span durations', table); cap.className = 'visually-hidden'; const head = el('thead', '', table), tr = el('tr', '', head); ['Span', 'Duration · ms', 'Status'].forEach(text => { const th = el('th', text, tr); th.scope = 'col'; }); const body = el('tbody', '', table); (trace.spans || []).forEach(span => { const row = el('tr', '', body); [span.name, String(span.duration_ms), span.status].forEach(value => el('td', value, row)); }); const details = el('details', '', root); el('summary', 'Bounded recorded request body', details); el('pre', JSON.stringify(request.body, null, 2).slice(0, 3500), details); }
function renderFacts() { if (!caseRecord)
    return; const root = $('facts'); clear(root); const filtered = caseRecord.evidence.facts.filter(f => $('signal').value === 'all' || f.kind === $('signal').value); if (!filtered.length) {
    root.textContent = 'No facts in this signal category.';
    return;
} filtered.forEach(f => { const section = el('article', '', root); section.className = 'disclosure'; section.id = `fact-${f.id}`; section.tabIndex = -1; el('strong', `${f.id} · ${f.kind}`, section); el('p', f.text, section); }); }
function focusFact(id) { $('signal').value = 'all'; renderFacts(); const fact = $(`fact-${id}`); if (fact) {
    fact.focus();
    fact.scrollIntoView({ block: 'center' });
} }
function investigate() { if (!caseRecord)
    return; const policy = $('policy').value, saved = caseRecord[policy], root = $('diagnosis'), repair = $('repair'); clear(root); clear(repair); el('p', `Saved ${policy} result · ${saved.state}. Nothing was inferred by this page.`, root); if (saved.state !== 'complete' || !saved.result) {
    el('p', `No validated proposal available: ${saved.error || 'recorded attempt failed'}. No repair plan.`, root);
    repair.textContent = 'No repair plan from a failed or rejected result.';
    return;
} const result = saved.result; if (!result.supports_verified || !result.status || !['requires_human_approval', 'no_repair_proposed'].includes(result.status)) {
    root.textContent = 'Saved result does not satisfy display validation. No proposal or repair shown.';
    repair.textContent = 'No repair plan from an unvalidated result.';
    return;
} el('h3', result.summary, root); el('p', `Saved status: ${result.status} · evidence digest: ${result.evidence_digest}`, root); if (result.abstain)
    el('p', 'Saved diagnosis abstains; no specific cause established.', root); for (const hypothesis of result.hypotheses || []) {
    const article = el('article', '', root);
    article.className = 'disclosure';
    el('h3', `${hypothesis.kind} · ${hypothesis.confidence} confidence`, article);
    el('p', hypothesis.statement, article);
    el('h3', 'Exact supporting fact citations', article);
    for (const support of hypothesis.supports || []) {
        const fact = caseRecord.evidence.facts.find(f => f.id === support.fact_id);
        if (!fact || !fact.text.includes(support.quote))
            continue;
        const p = el('p', '', article), b = el('button', `Focus ${support.fact_id}`, p);
        b.type = 'button';
        b.className = 'secondary';
        b.addEventListener('click', () => focusFact(support.fact_id));
        el('span', ` “${support.quote}”`, p);
    }
    el('h3', 'Alternatives', article);
    const alt = el('ul', '', article);
    (hypothesis.alternatives || []).forEach(text => el('li', text, alt));
} el('h3', 'Uncertainties', root); const list = el('ul', '', root); (result.uncertainty || []).forEach(text => el('li', text, list)); if (result.abstain || result.repair_key === 'none' || result.status === 'no_repair_proposed') {
    repair.textContent = 'No repair proposed. Collect additional aligned evidence before considering changes.';
}
else if (result.repair?.executable === false && result.repair?.requires_approval === true) {
    el('h3', `${result.repair.title} · ${result.status}`, repair);
    el('p', `Allowlisted file: ${result.repair.file || 'none'} · pending human review · nonexecutable`, repair);
    el('p', result.repair.proposed_change, repair);
    for (const [title, items] of [['Risks', result.repair.risks], ['Verification', result.repair.verification]]) {
        el('strong', title, repair);
        const ul = el('ul', '', repair);
        (items || []).forEach(item => el('li', item, ul));
    }
}
else
    repair.textContent = 'No valid human-review plan recorded.'; }
function exportCase() { if (!caseRecord)
    return; const blob = new Blob([JSON.stringify(caseRecord, null, 2)], { type: 'application/json' }), url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `faultline-${caseRecord.id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
setup();
fetch('./demo-data.json').then(response => { if (!response.ok)
    throw Error('Fixture unavailable'); return response.json(); }).then(json => { if (!Array.isArray(json.cases) || json.cases.length !== 5 || !json.cases.every(c => Array.isArray(c.outcomes) && c.outcomes.length === 6 && Array.isArray(c.telemetry) && Array.isArray(c.evidence?.facts)))
    throw Error('Unexpected incident fixture structure'); data = json; json.cases.forEach(c => $('case').add(new Option(c.label, c.id))); $('source-attribution').textContent = `USGS frozen GeoJSON ${json.source_url} · SHA-256 ${json.study_summary.source_sha256}; case evidence carries exact local capture timestamps.`; $('status').textContent = `${json.cases.length} recorded cases · ${json.study_summary.validated_matching_conditions}/${json.study_summary.frozen_conditions} validated frozen conditions · no model calls.`; selectCase(); }).catch(error => { $('status').textContent = `Cannot load incident fixture: ${error.message}. Reload to try again.`; $('status').className = 'status error'; });
function labelScrollableContent() {
    document.querySelectorAll('.scroll, pre').forEach(node => {
        node.tabIndex = 0;
        if (!node.hasAttribute('role'))
            node.setAttribute('role', 'region');
        if (!node.hasAttribute('aria-label'))
            node.setAttribute('aria-label', `${node.closest('section')?.querySelector('h2')?.textContent || 'Recorded'} scrollable content`);
    });
}
new MutationObserver(labelScrollableContent).observe(document.getElementById('main'), { childList: true, subtree: true });
labelScrollableContent();
