/**
 * AgentX Code Parser
 * 
 * Regex-based symbol extraction — lightweight, no AST dependency.
 * Parses: functions, classes, interfaces, types, imports, exports,
 *         routes, tests, schemas, React components.
 * 
 * Supports: TypeScript, JavaScript, Python
 */

import { createHash } from 'crypto';
import { readFileSync } from 'fs';
import path from 'path';
import type { GraphNode, GraphEdge, FileParseResult, NodeKind } from './types.js';

// ── Language Detection ────────────────────────────────────────

type Language = 'typescript' | 'javascript' | 'python' | 'json' | 'unknown';

function detectLanguage(filePath: string): Language {
  const ext = path.extname(filePath).toLowerCase();
  const map: Record<string, Language> = {
    '.ts': 'typescript', '.tsx': 'typescript', '.mts': 'typescript',
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
    '.py': 'python',
    '.json': 'json',
  };
  return map[ext] || 'unknown';
}

// ── Hash ──────────────────────────────────────────────────────

function hashContent(content: string): string {
  return createHash('sha256').update(content).digest('hex').slice(0, 16);
}

// ── Regex Patterns ────────────────────────────────────────────

const TS_PATTERNS = {
  // Functions & arrow functions
  function: /^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)/gm,
  arrowExport: /^export\s+(?:const|let)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>/gm,
  arrowConst: /^(?:const|let)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>/gm,

  // Classes
  class: /^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?/gm,
  method: /^\s+(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?(\w+)\s*\(([^)]*)\)/gm,

  // Interfaces & types
  interface: /^(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([^{]+))?/gm,
  typeAlias: /^(?:export\s+)?type\s+(\w+)\s*(?:<[^>]*>)?\s*=/gm,

  // Imports
  importFrom: /import\s+(?:{([^}]+)}|(\w+))\s+from\s+['"]([^'"]+)['"]/gm,
  importDefault: /import\s+(\w+)\s+from\s+['"]([^'"]+)['"]/gm,

  // Exports
  exportNamed: /^export\s+(?:const|let|var|function|class|interface|type|enum)\s+(\w+)/gm,
  exportDefault: /^export\s+default\s+(?:class|function)?\s*(\w+)?/gm,

  // Routes (Express/Fastify/Hono style)
  route: /(?:app|router|server)\.(get|post|put|patch|delete|all)\s*\(\s*['"`]([^'"`]+)['"`]/gm,

  // Tests (jest/vitest/mocha)
  test: /(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)['"`]/gm,

  // React components
  component: /^(?:export\s+)?(?:const|function)\s+([A-Z]\w+)\s*(?::\s*React\.FC)?/gm,

  // Schema/Model (Prisma, Drizzle, Mongoose, Zod)
  schema: /(?:createTable|pgTable|mysqlTable|sqliteTable|mongoose\.model|z\.object)\s*\(\s*['"`](\w+)['"`]/gm,
  prismaModel: /^model\s+(\w+)\s*\{/gm,
};

const PY_PATTERNS = {
  function: /^(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)/gm,
  class: /^class\s+(\w+)(?:\(([^)]*)\))?/gm,
  import: /^(?:from\s+(\S+)\s+)?import\s+(.+)/gm,
  decorator: /^@(\w+)/gm,
  route: /@(?:app|router|bp)\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]/gm,
  test: /^(?:async\s+)?def\s+(test_\w+)\s*\(/gm,
  schema: /^class\s+(\w+)\((?:.*?Model|.*?Base|.*?Schema)\)/gm,
};

// ── Parser ────────────────────────────────────────────────────

export function parseFile(filePath: string, cwd: string): FileParseResult {
  const fullPath = path.isAbsolute(filePath) ? filePath : path.join(cwd, filePath);
  const relPath = path.relative(cwd, fullPath).replace(/\\/g, '/');
  const language = detectLanguage(filePath);

  let content: string;
  try {
    content = readFileSync(fullPath, 'utf8');
  } catch {
    return { filePath: relPath, hash: '', nodes: [], edges: [], language };
  }

  const hash = hashContent(content);
  const lines = content.split('\n');
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  // File node
  const fileId = `file:${relPath}`;
  nodes.push({
    id: fileId,
    kind: 'file',
    name: path.basename(relPath),
    filePath: relPath,
    exported: true,
    metadata: { language, lineCount: lines.length, size: content.length },
  });

  if (language === 'typescript' || language === 'javascript') {
    parseTSJS(content, lines, relPath, fileId, nodes, edges);
  } else if (language === 'python') {
    parsePython(content, lines, relPath, fileId, nodes, edges);
  } else if (language === 'json') {
    parseJSON(relPath, content, fileId, nodes, edges);
  }

  return { filePath: relPath, hash, nodes, edges, language };
}

// ── TypeScript / JavaScript ───────────────────────────────────

function parseTSJS(
  content: string, lines: string[], filePath: string, fileId: string,
  nodes: GraphNode[], edges: GraphEdge[]
): void {
  // Functions
  for (const match of content.matchAll(TS_PATTERNS.function)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `fn:${filePath}:${name}`;
    const exported = match[0].startsWith('export');
    nodes.push({ id, kind: 'function', name, filePath, line, exported, signature: match[2] });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
  }

  // Arrow exports
  for (const match of content.matchAll(TS_PATTERNS.arrowExport)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `fn:${filePath}:${name}`;
    if (!nodes.some(n => n.id === id)) {
      nodes.push({ id, kind: 'function', name, filePath, line, exported: true });
      edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
    }
  }

  // Classes
  for (const match of content.matchAll(TS_PATTERNS.class)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `class:${filePath}:${name}`;
    const exported = match[0].startsWith('export');
    nodes.push({ id, kind: 'class', name, filePath, line, exported });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });

    // Extends
    if (match[2]) {
      edges.push({ source: id, target: `class:*:${match[2].trim()}`, kind: 'extends', weight: 2 });
    }
  }

  // Methods (inside classes — simplified: capture all method-like patterns)
  for (const match of content.matchAll(TS_PATTERNS.method)) {
    const name = match[1];
    if (['constructor', 'if', 'else', 'for', 'while', 'switch', 'return', 'try', 'catch'].includes(name)) continue;
    const line = lineOf(content, match.index!);
    const id = `method:${filePath}:${name}`;
    if (!nodes.some(n => n.id === id)) {
      nodes.push({ id, kind: 'method', name, filePath, line, exported: false, signature: match[2] });
    }
  }

  // Interfaces
  for (const match of content.matchAll(TS_PATTERNS.interface)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `iface:${filePath}:${name}`;
    const exported = match[0].startsWith('export');
    nodes.push({ id, kind: 'interface', name, filePath, line, exported });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
    if (match[2]) {
      for (const ext of match[2].split(',').map(s => s.trim())) {
        if (ext) edges.push({ source: id, target: `iface:*:${ext}`, kind: 'extends', weight: 2 });
      }
    }
  }

  // Type aliases
  for (const match of content.matchAll(TS_PATTERNS.typeAlias)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `type:${filePath}:${name}`;
    const exported = match[0].startsWith('export');
    nodes.push({ id, kind: 'type', name, filePath, line, exported });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
  }

  // Imports → edges
  for (const match of content.matchAll(TS_PATTERNS.importFrom)) {
    const importedNames = (match[1] || match[2] || '').split(',').map(s => s.trim().split(' as ')[0].trim()).filter(Boolean);
    const source = match[3];
    const resolvedSource = resolveImportPath(source, filePath);
    edges.push({ source: fileId, target: `file:${resolvedSource}`, kind: 'imports', weight: 1 });
    for (const imported of importedNames) {
      edges.push({ source: fileId, target: `*:${resolvedSource}:${imported}`, kind: 'uses', weight: 1 });
    }
  }

  // Routes
  for (const match of content.matchAll(TS_PATTERNS.route)) {
    const method = match[1].toUpperCase();
    const routePath = match[2];
    const line = lineOf(content, match.index!);
    const id = `route:${filePath}:${method}:${routePath}`;
    nodes.push({ id, kind: 'route', name: `${method} ${routePath}`, filePath, line, exported: true, metadata: { method, path: routePath } });
    edges.push({ source: fileId, target: id, kind: 'routes_to', weight: 2 });
  }

  // Tests
  for (const match of content.matchAll(TS_PATTERNS.test)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `test:${filePath}:${name}`;
    nodes.push({ id, kind: 'test', name, filePath, line, exported: false });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
  }

  // React components (starts with capital letter)
  for (const match of content.matchAll(TS_PATTERNS.component)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `component:${filePath}:${name}`;
    if (!nodes.some(n => n.name === name)) {
      nodes.push({ id, kind: 'component', name, filePath, line, exported: match[0].includes('export') });
      edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
    }
  }

  // Schemas (Zod, Prisma-like)
  for (const match of content.matchAll(TS_PATTERNS.schema)) {
    const name = match[1];
    if (['object', 'string', 'number', 'boolean', 'array'].includes(name)) continue;
    const line = lineOf(content, match.index!);
    const id = `schema:${filePath}:${name}`;
    if (!nodes.some(n => n.id === id)) {
      nodes.push({ id, kind: 'schema', name, filePath, line, exported: false });
      edges.push({ source: fileId, target: id, kind: 'schema_of', weight: 2 });
    }
  }

  // Function calls — detect known symbol references
  detectCalls(content, filePath, fileId, nodes, edges);
}

// ── Python ────────────────────────────────────────────────────

function parsePython(
  content: string, _lines: string[], filePath: string, fileId: string,
  nodes: GraphNode[], edges: GraphEdge[]
): void {
  for (const match of content.matchAll(PY_PATTERNS.function)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const isTest = name.startsWith('test_');
    const id = `${isTest ? 'test' : 'fn'}:${filePath}:${name}`;
    nodes.push({ id, kind: isTest ? 'test' : 'function', name, filePath, line, exported: true, signature: match[2] });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
  }

  for (const match of content.matchAll(PY_PATTERNS.class)) {
    const name = match[1];
    const line = lineOf(content, match.index!);
    const id = `class:${filePath}:${name}`;
    nodes.push({ id, kind: 'class', name, filePath, line, exported: true });
    edges.push({ source: fileId, target: id, kind: 'defines', weight: 1 });
    if (match[2]) {
      for (const base of match[2].split(',').map(s => s.trim())) {
        if (base && !['object', 'Exception'].includes(base)) {
          edges.push({ source: id, target: `class:*:${base}`, kind: 'extends', weight: 2 });
        }
      }
    }
  }

  for (const match of content.matchAll(PY_PATTERNS.import)) {
    const moduleName = match[1] || match[2];
    if (moduleName) {
      edges.push({ source: fileId, target: `file:${moduleName.replace(/\./g, '/')}`, kind: 'imports', weight: 1 });
    }
  }

  for (const match of content.matchAll(PY_PATTERNS.route)) {
    const method = match[1].toUpperCase();
    const routePath = match[2];
    const line = lineOf(content, match.index!);
    const id = `route:${filePath}:${method}:${routePath}`;
    nodes.push({ id, kind: 'route', name: `${method} ${routePath}`, filePath, line, exported: true });
    edges.push({ source: fileId, target: id, kind: 'routes_to', weight: 2 });
  }
}

// ── JSON (package.json, schemas) ──────────────────────────────

function parseJSON(filePath: string, content: string, fileId: string, nodes: GraphNode[], edges: GraphEdge[]): void {
  if (filePath.endsWith('package.json')) {
    try {
      const pkg = JSON.parse(content);
      const deps = { ...pkg.dependencies, ...pkg.devDependencies };
      for (const dep of Object.keys(deps)) {
        edges.push({ source: fileId, target: `pkg:${dep}`, kind: 'imports', weight: 1 });
      }
    } catch {}
  }
}

// ── Call Detection ────────────────────────────────────────────

function detectCalls(content: string, filePath: string, fileId: string, nodes: GraphNode[], edges: GraphEdge[]): void {
  // Find function calls matching known nodes
  const knownFunctions = nodes.filter(n => n.kind === 'function' || n.kind === 'method').map(n => n.name);
  const callPattern = /(?<!\w)(\w+)\s*\(/g;

  for (const match of content.matchAll(callPattern)) {
    const name = match[1];
    if (knownFunctions.includes(name) && !['if', 'for', 'while', 'switch', 'catch', 'return', 'import', 'require', 'console', 'new'].includes(name)) {
      const target = nodes.find(n => n.name === name && (n.kind === 'function' || n.kind === 'method'));
      if (target && target.id !== fileId) {
        const exists = edges.some(e => e.source === fileId && e.target === target.id && e.kind === 'calls');
        if (!exists) {
          edges.push({ source: fileId, target: target.id, kind: 'calls', weight: 1 });
        }
      }
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────

function lineOf(content: string, index: number): number {
  return content.slice(0, index).split('\n').length;
}

function resolveImportPath(importPath: string, currentFile: string): string {
  if (importPath.startsWith('.')) {
    const dir = path.dirname(currentFile);
    let resolved = path.posix.join(dir, importPath);
    // Add extension if missing
    if (!path.extname(resolved)) resolved += '.ts';
    // Remove .js → .ts mapping
    resolved = resolved.replace(/\.js$/, '.ts');
    return resolved;
  }
  // Node module — keep as-is
  return `node_modules/${importPath}`;
}
