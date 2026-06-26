'use strict';

// ── Minimal DOM polyfill ──────────────────────────────────────────────────
// exsurge.ChantContext() uses document for canvas creation and font injection.
// We use TextMeasuringStrategy.Canvas so getBBox() (Svg strategy) is never called.
// Canvas creation is redirected to OffscreenCanvas, which workers support natively.

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

class FakeElement {
  constructor(tag, ns) {
    this.tagName = this.localName = this.nodeName = tag;
    this.namespaceURI = ns || null;
    this.nodeType = 1; this.id = '';
    this._a = Object.create(null);
    this._c = [];
    this.style = Object.create(null);
    this.textContent = '';
  }
  setAttribute(k, v)   { this._a[k] = v == null ? '' : String(v); }
  getAttribute(k)       { return Object.hasOwn(this._a, k) ? this._a[k] : null; }
  hasAttribute(k)       { return Object.hasOwn(this._a, k); }
  removeAttribute(k)    { delete this._a[k]; }
  appendChild(c)        { this._c.push(c); return c; }
  insertBefore(c, ref)  { const i=this._c.indexOf(ref); i<0?this._c.push(c):this._c.splice(i,0,c); return c; }
  removeChild(c)        { const i=this._c.indexOf(c); if(i>=0)this._c.splice(i,1); return c; }
  cloneNode(deep)       { const n=new FakeElement(this.tagName,this.namespaceURI); Object.assign(n._a,this._a); if(deep)n._c=this._c.map(c=>c.cloneNode?.(true)??c); return n; }
  get firstChild()      { return this._c[0] ?? null; }
  get lastChild()       { return this._c[this._c.length-1] ?? null; }
  get childNodes()      { return this._c; }
  get children()        { return this._c.filter(c=>c.nodeType===1); }
  querySelector()       { return null; }
  querySelectorAll()    { return []; }
  addEventListener()    {}
  removeEventListener() {}
  get innerHTML()       { return this._c.map(c=>c.outerHTML??c.textContent??'').join(''); }
  set innerHTML(v)      { this._c = v ? [{ outerHTML: v, textContent: v }] : []; }
  get outerHTML() {
    let attrs = '';
    for (const [k,v] of Object.entries(this._a)) attrs += ` ${k}="${_esc(v)}"`;
    const inner = this.textContent + this._c.map(c=>c.outerHTML??_esc(c.textContent??'')).join('');
    return inner ? `<${this.tagName}${attrs}>${inner}</${this.tagName}>` : `<${this.tagName}${attrs}/>`;
  }
}

class FakeTextNode {
  constructor(t) { this.nodeType=3; this.nodeName='#text'; this.textContent=t; }
  get outerHTML() { return _esc(this.textContent); }
  cloneNode()     { return new FakeTextNode(this.textContent); }
}

const _sink = { appendChild(){}, removeChild(){}, firstChild: null, hasChildNodes(){ return false; } };

self.document = {
  createElementNS: (ns, tag) => new FakeElement(tag, ns),
  createElement(tag) {
    if (tag === 'canvas') {
      try {
        const c = new OffscreenCanvas(300, 150);
        c.style = {};
        return c;
      } catch(_) { /* OffscreenCanvas not available; fall through */ }
    }
    return new FakeElement(tag);
  },
  createTextNode:        t  => new FakeTextNode(t),
  createDocumentFragment:() => new FakeElement('#fragment'),
  getElementById:        () => null,
  querySelector:         () => null,
  querySelectorAll:      () => [],
  head: _sink,
  body: _sink,
};

self.window    = self;
self.devicePixelRatio = 1;
self.Element   = self.HTMLElement = self.SVGElement = FakeElement;
self.XMLSerializer = class { serializeToString(el) { return el.outerHTML ?? ''; } };
self.getComputedStyle  = () => ({ getPropertyValue: () => '' });
self.performance = self.performance || { now: () => Date.now() };

// ── Load exsurge ─────────────────────────────────────────────────────────

importScripts('/exsurge.js');

// ── Helpers ───────────────────────────────────────────────────────────────

const ROMAN_MODE = ['','I','II','III','IV','V','VI','VII','VIII'];

function modeAnnotation(mode) {
  if (!mode) return null;
  const m = String(mode).match(/^(\d+)(.*)/);
  if (!m) return null;
  const n = parseInt(m[1]);
  return (n >= 1 && n <= 8) ? ROMAN_MODE[n] + m[2] : null;
}

function cleanGabc(g) {
  return (g || '')
    .replace(/\[[^\]]*\]/g, '')
    .replace(/<sp>([VRAvra])\/<\/sp>\.?/g, '$1/.')
    .replace(/<sp>'?(?:ae|æ)<\/sp>/g, 'ǽ')
    .replace(/<sp>'?(?:oe|œ)<\/sp>/g, 'œ')
    .replace(/<i>([^<]*)<\/i>/g, '_$1_')
    .replace(/<sc>([^<]*)<\/sc>/g, '%$1%')
    .replace(/<[^>]+>/g, '')
    .replace(/\*(?!\()/g, '*()');
}

function prepareNotation(gabc) {
  let s = (gabc || '')
    .replace(/<sp>([VRAvra])\/<\/sp>\.?/g, '$1/.')
    .replace(/<sp>[^<]*<\/sp>/g, '')
    .replace(/<i>([^<]*)<\/i>/g, '_$1_')
    .replace(/<sc>([^<]*)<\/sc>/g, '%$1%')
    .replace(/<[^>]+>/g, '');
  let modeStr = null;
  const sep = s.indexOf('%%');
  if (sep !== -1) {
    const header = s.slice(0, sep);
    const mm = header.match(/\bmode\s*:\s*(\S+)/i);
    if (mm) modeStr = mm[1].replace(/[;\s]+$/, '');
    s = s.slice(sep + 2);
  }
  return { notation: s, modeStr };
}

// ── Message handler ───────────────────────────────────────────────────────

// Track the most recent gen so self.onerror can report it
let _lastGen = 0;

// Catch any exception that escapes all try/catch blocks (e.g. from setTimeout chains).
// Returning true suppresses propagation to the main thread's _worker.onerror.
self.onerror = function(e) {
  console.error('[worker:onerror]', e.message, 'at line', e.lineno);
  self.postMessage({ gen: _lastGen, svg: null, error: '[onerror] ' + e.message });
  return true;
};

self.onmessage = function({ data: { gabc, width, gen } }) {
  _lastGen = gen;
  const { notation, modeStr } = prepareNotation(gabc);
  if (!notation.trim()) { self.postMessage({ gen, svg: null }); return; }
  try {
    // Canvas strategy avoids getBBox() which requires a live browser SVG context
    const ctxt = new exsurge.ChantContext(exsurge.TextMeasuringStrategy.Canvas);
    ctxt.lyricTextFont = 'Georgia, serif';
    ctxt.dropCapTextFont = ctxt.lyricTextFont;
    ctxt.lyricTextSize *= 1.15;

    const mappings = exsurge.Gabc.createMappingsFromSource(ctxt, cleanGabc(notation));
    console.log('[worker] mappings:', mappings.length,
      '| any non-ChantMapping:', mappings.some(m => !m || !Array.isArray(m.notations)));
    const score = new exsurge.ChantScore(ctxt, mappings, true);
    console.log('[worker] score.notations:', score.notations.length,
      '| any null/undef:', score.notations.some(n => n == null));

    const ann = modeAnnotation(modeStr);
    if (ann && exsurge.Annotation) score.annotation = new exsurge.Annotation(ctxt, ann);

    // Use synchronous performLayout — workers don't block the UI thread regardless,
    // so there is no reason to use performLayoutAsync. The async version spawns
    // setTimeout chains whose throws escape any surrounding try/catch.
    score.performLayout(ctxt);
    console.log('[worker] performLayout done');

    score.notations = score.notations.filter(n => n != null);
    if (score.notations.length === 0) {
      console.log('[worker] notations empty after filter — returning null svg');
      self.postMessage({ gen, svg: null });
      return;
    }

    console.log('[worker] calling layoutChantLines with', score.notations.length, 'notations, width', width);
    score.layoutChantLines(ctxt, width, () => {
      console.log('[worker] layoutChantLines done — creating SVG');
      self.postMessage({ gen, svg: score.createSvg(ctxt) });
    });
    console.log('[worker] layoutChantLines returned');
  } catch(e) {
    console.error('[worker] outer error:', e);
    self.postMessage({ gen, svg: null, error: String(e) });
  }
};
