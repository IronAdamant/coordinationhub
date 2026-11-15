// Runs the dashboard page's own script against a live server, with the
// few browser objects it touches stubbed, and prints one JSON line of page
// state every 250 ms. tests/http_security.rs drives it (when `node` is
// installed) to show that the page gets data with auth on.
//
// usage: node dashboard_harness.js <base url> <fragment or -> <seconds>
'use strict';

const [base, fragmentArg, secondsArg] = process.argv.slice(2);
const fragment = fragmentArg === '-' ? '' : fragmentArg;
const seconds = Number(secondsArg || '5');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      textContent: '',
      innerHTML: '',
      hidden: id === 'notice',
      clientWidth: 800,
      clientHeight: 320,
      classList: { add() {}, remove() {} },
      addEventListener() {},
      setAttribute() {},
      querySelector() { return null; },
      getBoundingClientRect() { return { left: 0, top: 0 }; },
    });
  }
  return elements.get(id);
}

const requests = [];
const realFetch = globalThis.fetch;
function pageFetch(url, options) {
  const headers = (options && options.headers) || {};
  requests.push({ url: String(url), authorization: headers.Authorization || null });
  return realFetch(new URL(url, base), options);
}
globalThis.fetch = pageFetch;

const stored = new Map();
const location = { hash: fragment, pathname: '/', search: '' };
const window = {
  fetch: pageFetch,
  ReadableStream: globalThis.ReadableStream,
  TextDecoder: globalThis.TextDecoder,
  AbortController: globalThis.AbortController,
  location,
  history: {
    replaceState(_state, _title, url) {
      const at = String(url).indexOf('#');
      location.hash = at >= 0 ? String(url).slice(at) : '';
    },
  },
  sessionStorage: {
    getItem(key) { return stored.has(key) ? stored.get(key) : null; },
    setItem(key, value) { stored.set(key, String(value)); },
  },
  addEventListener() {},
};
const document = { getElementById: element };

function snapshot() {
  return {
    timestamp: element('timestamp').textContent,
    notice: element('notice').textContent,
    noticeHidden: element('notice').hidden,
    tasks: element('task-tbody').innerHTML,
    locks: element('lock-list').innerHTML,
    hash: location.hash,
    stored: stored.get('coordhub-token') || null,
    requests: requests.slice(),
  };
}

(async () => {
  const page = await (await realFetch(new URL('/', base))).text();
  const match = /<script>([\s\S]*?)<\/script>/.exec(page);
  if (!match) throw new Error('the page has no inline script');
  new Function('window', 'document', match[1])(window, document);
  const started = Date.now();
  const timer = setInterval(() => {
    process.stdout.write(JSON.stringify(snapshot()) + '\n');
    if (Date.now() - started > seconds * 1000) {
      clearInterval(timer);
      process.exit(0);
    }
  }, 250);
})().catch((err) => {
  process.stderr.write(String((err && err.stack) || err) + '\n');
  process.exit(1);
});
