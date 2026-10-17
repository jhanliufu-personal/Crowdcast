// Static check for model-written spell code (object builders and behaviors), before it goes near a page.
//
//   echo '{"code": "...", "kind": "behavior"}' | node harness/codecheck/check.mjs
//   → {"ok": true, "code": "<the code with runtime guards added>"} or {"ok": false, "errors": ["line 3: ..."]}
//
// The code is the body of a function the spell runtime calls:
//   object:   function (THREE, api, params)        → returns the Object3D to throw
//   behavior: function (THREE, api, spell, params) → registers per-frame logic with spell.onFrame
// It may only read the names in rules.json "globals" plus what it declares itself, and never a property in
// "deny_members". What can't be checked statically gets a runtime guard instead:
//   obj[key]  becomes obj[__k(key)]: __k lets numbers and plain names through and throws on denied names, so a
//             string built at runtime ("constr" + "uctor") can't reach Function.
//   loops and function bodies call __loop(), which throws after too many steps in one frame.
// The runtime supplies __k and __loop; the model's code can't name anything starting with "__".

import { parse } from '../../vendor/acorn/acorn.mjs';
import { readFileSync } from 'node:fs';

const rules = JSON.parse(readFileSync(new URL('./rules.json', import.meta.url), 'utf8'));
const GLOBALS = new Set(rules.globals);
const DENY = new Set(rules.deny_members);
const DENY_RE = new RegExp(rules.deny_member_pattern);
const PARAMS = { object: ['THREE', 'api', 'params'], behavior: ['THREE', 'api', 'spell', 'params'] };
const BANNED_NODES = {
  ThisExpression: '`this` is not available',
  ImportExpression: 'import() is not allowed',
  MetaProperty: 'new.target / import.meta are not allowed',
  WhileStatement: 'use a for loop with a fixed count, not while',
  DoWhileStatement: 'use a for loop with a fixed count, not do/while',
  WithStatement: '`with` is not allowed',
  AwaitExpression: 'the code must be synchronous',
  YieldExpression: 'generators are not allowed',
  ClassDeclaration: 'classes are not allowed; use plain functions and objects',
  ClassExpression: 'classes are not allowed; use plain functions and objects',
  TaggedTemplateExpression: 'tagged templates are not allowed',
  DebuggerStatement: '`debugger` is not allowed',
  Super: '`super` is not allowed',
};

function check(code, kind) {
  const errors = [];
  if (typeof code !== 'string' || !code.trim()) return { ok: false, errors: ['no code'] };
  if (code.length > rules.max_chars) errors.push(`code is ${code.length} characters; the limit is ${rules.max_chars}`);
  if (!PARAMS[kind]) return { ok: false, errors: [`unknown kind ${kind}`] };

  const prefix = `(function (${PARAMS[kind].join(', ')}) {\n'use strict';\n`;
  const source = `${prefix}${code}\n})`;
  let ast;
  try {
    ast = parse(source, { ecmaVersion: 2022, sourceType: 'script', locations: true });
  } catch (err) {
    const line = err.loc ? err.loc.line - 2 : '?';
    return { ok: false, errors: [`line ${line}: syntax error: ${err.message.replace(/ \(\d+:\d+\)$/, '')}`] };
  }
  // The code must stay inside the wrapper: "}); evil(); (function () {" parses too, as three statements.
  const wrapper = ast.body[0]?.expression;
  if (ast.body.length !== 1 || wrapper?.type !== 'FunctionExpression' || wrapper.end !== source.length - 1) {
    return { ok: false, errors: ['the code must be one function body: it closes the function early'] };
  }
  const at = (node) => `line ${node.loc.start.line - 2}`;
  const fail = (node, message) => errors.push(`${at(node)}: ${message}`);

  // Every name the code declares, anywhere (a flat set is stricter than real scoping, never looser:
  // it can only let through names the code itself created).
  const declared = new Set(PARAMS[kind]);
  const declare = (pattern) => {
    if (!pattern) return;
    if (pattern.type === 'Identifier') declared.add(pattern.name);
    else if (pattern.type === 'ObjectPattern') pattern.properties.forEach((p) => declare(p.type === 'RestElement' ? p.argument : p.value));
    else if (pattern.type === 'ArrayPattern') pattern.elements.forEach(declare);
    else if (pattern.type === 'RestElement') declare(pattern.argument);
    else if (pattern.type === 'AssignmentPattern') declare(pattern.left);
  };

  const inserts = []; // [position in source, text]
  const nodes = [];
  (function walk(node, parent, key) {
    if (!node || typeof node.type !== 'string') return;
    node.parent = parent;
    node.parentKey = key;
    nodes.push(node);
    for (const k of Object.keys(node)) {
      if (k === 'parent' || k === 'loc') continue;
      const child = node[k];
      if (Array.isArray(child)) child.forEach((c) => walk(c, node, k));
      else if (child && typeof child.type === 'string') walk(child, node, k);
    }
  })(ast.body[0].expression, null, null);

  const outer = ast.body[0].expression; // the wrapper function itself
  for (const node of nodes) {
    if (node.type === 'VariableDeclarator') declare(node.id);
    if (/Function/.test(node.type)) {
      if (node.id) declared.add(node.id.name);
      node.params.forEach(declare);
    }
    if (node.type === 'CatchClause') declare(node.param);
  }

  for (const node of nodes) {
    if (BANNED_NODES[node.type]) fail(node, BANNED_NODES[node.type]);
    if (/Function/.test(node.type) && node !== outer && (node.async || node.generator)) fail(node, 'async functions and generators are not allowed');

    if (node.type === 'Identifier') {
      const p = node.parent;
      const isPropertyName = (p.type === 'MemberExpression' && p.property === node && !p.computed)
        || (p.type === 'Property' && p.key === node && !p.computed && !(p.shorthand && p.parent.type === 'ObjectExpression'))
        || (p.type === 'MethodDefinition' && p.key === node);
      const isLabel = ['LabeledStatement', 'BreakStatement', 'ContinueStatement'].includes(p.type);
      if (node.name.startsWith('__')) fail(node, `names starting with "__" are reserved (${node.name})`);
      if (isLabel) {
        // a statement label, not a variable
      } else if (isPropertyName) {
        if (DENY.has(node.name) || DENY_RE.test(node.name)) fail(node, `property "${node.name}" is not allowed`);
      } else if (!declared.has(node.name) && !GLOBALS.has(node.name)) {
        fail(node, `"${node.name}" is not available (use THREE, api, spell, params, Math, or your own variables)`);
      }
    }

    if (node.type === 'Property' && node.parent.type === 'ObjectPattern' && node.computed) {
      fail(node, 'computed keys in destructuring are not allowed');
    }
    if (node.type === 'Literal' && typeof node.value === 'string' && /constructor|__proto__|prototype/.test(node.value)) {
      fail(node, 'that string is not allowed');
    }
    if (node.type === 'TemplateElement' && /constructor|__proto__|prototype/.test(node.value.cooked ?? '')) {
      fail(node, 'that string is not allowed');
    }

    // Runtime guards.
    if (node.type === 'MemberExpression' && node.computed) {
      const key = node.property;
      if (!(key.type === 'Literal' && typeof key.value === 'number')) {
        inserts.push([key.start, '__k('], [key.end, ')']);
      }
    }
    if (['ForStatement', 'ForOfStatement', 'ForInStatement'].includes(node.type)) guardBody(node.body);
    if (/Function/.test(node.type) && node !== outer) {
      if (node.body.type === 'BlockStatement') inserts.push([node.body.start + 1, '__loop();']);
      else inserts.push([node.body.start, '(__loop(), '], [node.body.end, ')']);
    }
  }
  function guardBody(body) {
    if (body.type === 'BlockStatement') inserts.push([body.start + 1, '__loop();']);
    else inserts.push([body.start, '{__loop();'], [body.end, '}']);
  }

  if (kind === 'object' && !nodes.some((n) => n.type === 'ReturnStatement' && enclosingFunction(n) === outer)) {
    errors.push('an object builder must return the object (return group;)');
  }
  function enclosingFunction(node) {
    let n = node.parent;
    while (n && !/Function/.test(n.type)) n = n.parent;
    return n;
  }

  if (errors.length) return { ok: false, errors: [...new Set(errors)].slice(0, 12) };
  let guarded = source;
  // Apply from the end so earlier positions stay valid; at equal positions keep insertion order.
  inserts.map((ins, i) => [...ins, i]).sort((a, b) => b[0] - a[0] || b[2] - a[2]).forEach(([pos, text]) => {
    guarded = guarded.slice(0, pos) + text + guarded.slice(pos);
  });
  const body = guarded.slice(prefix.length, guarded.length - '\n})'.length);
  return { ok: true, code: body };
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  let result;
  try {
    const { code, kind } = JSON.parse(input);
    result = check(code, kind);
  } catch (err) {
    result = { ok: false, errors: [`checker error: ${err.message}`] };
  }
  process.stdout.write(JSON.stringify(result));
});
