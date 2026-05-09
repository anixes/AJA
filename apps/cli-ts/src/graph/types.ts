/**
 * AgentX Graph Types
 * 
 * The repo brain stores code as a directed graph:
 *   Nodes = symbols (files, functions, classes, routes, tests, schemas)
 *   Edges = relationships (imports, calls, extends, tests, defines)
 */

// ── Node Types ────────────────────────────────────────────────

export type NodeKind =
  | 'file'
  | 'function'
  | 'class'
  | 'method'
  | 'interface'
  | 'type'
  | 'variable'
  | 'export'
  | 'route'       // API endpoint
  | 'test'        // Test case/suite
  | 'schema'      // DB model/schema
  | 'component';  // React/UI component

export interface GraphNode {
  id: string;              // Unique: "file:src/foo.ts" or "fn:src/foo.ts:handleClick"
  kind: NodeKind;
  name: string;            // Human label: "handleClick"
  filePath: string;        // Relative path
  line?: number;           // Start line
  endLine?: number;        // End line
  signature?: string;      // e.g. "(req: Request, res: Response) => void"
  docstring?: string;      // Leading comment/jsdoc
  exported: boolean;       // Is this exported?
  metadata?: Record<string, unknown>;
}

// ── Edge Types ────────────────────────────────────────────────

export type EdgeKind =
  | 'imports'     // A imports B
  | 'calls'       // A calls B
  | 'extends'     // A extends/implements B
  | 'defines'     // File defines symbol
  | 'tests'       // Test tests symbol
  | 'uses'        // General dependency
  | 'routes_to'   // Route handler
  | 'schema_of';  // Schema defines model

export interface GraphEdge {
  source: string;   // Node ID
  target: string;   // Node ID
  kind: EdgeKind;
  weight: number;   // 1 = normal, higher = stronger relationship
  metadata?: Record<string, unknown>;
}

// ── Graph Container ───────────────────────────────────────────

export interface RepoGraph {
  version: number;
  projectRoot: string;
  indexedAt: string;            // ISO timestamp
  fileHashes: Record<string, string>;  // path → content hash (for incremental)
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
}

export interface GraphStats {
  totalFiles: number;
  totalNodes: number;
  totalEdges: number;
  totalFunctions: number;
  totalClasses: number;
  totalRoutes: number;
  totalTests: number;
  totalSchemas: number;
  indexDurationMs: number;
}

// ── Query Results ─────────────────────────────────────────────

export interface TraceResult {
  /** The path of nodes from source to target */
  path: GraphNode[];
  /** Edges connecting the path */
  edges: GraphEdge[];
  /** Explanation of the trace */
  summary: string;
}

export interface ImpactResult {
  /** The changed file/symbol */
  source: GraphNode;
  /** Directly affected nodes */
  directDeps: GraphNode[];
  /** Transitively affected nodes (depth 2+) */
  transitiveDeps: GraphNode[];
  /** Risk score 0-100 */
  riskScore: number;
  /** Human-readable summary */
  summary: string;
}

export interface RetrievalResult {
  /** Ranked list of relevant files with their content */
  files: Array<{
    path: string;
    relevance: number;   // 0-1
    content: string;
    symbols: string[];   // Key symbols in the file
  }>;
  /** Total tokens in the context */
  estimatedTokens: number;
}

// ── Parse Result (per file) ───────────────────────────────────

export interface FileParseResult {
  filePath: string;
  hash: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  language: 'typescript' | 'javascript' | 'python' | 'json' | 'unknown';
}
