import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import path from 'path';

export interface RuntimeApproval {
  id: string;
  tool: string;
  input: Record<string, unknown>;
  command?: string;
  commandPreview?: string;
  actionType?: string;
  rootBinary?: string;
  level?: string;
  riskLevel?: 'low' | 'medium' | 'high';
  reasons: string[];
  humanReason?: string;
  rollbackPath?: string;
  expiresAt?: string;
  requesterSource?: 'CLI' | 'dashboard' | 'Telegram' | 'swarm';
  dryRunSummary?: string;
  createdAt: string;
}

export interface RuntimeEvent {
  id: string;
  type: 'ALLOW' | 'ASK' | 'DENY' | 'APPROVED' | 'DENIED' | 'INFO';
  tool: string;
  message: string;
  command?: string;
  rootBinary?: string;
  level?: string;
  createdAt: string;
}

export interface RuntimeStateShape {
  pendingApproval: RuntimeApproval | null;
  events: RuntimeEvent[];
  tokenStats: {
    total: number;
    saved: number;
    lastTurn: number;
  };
}

const STATE_DIR = path.join(process.cwd(), '.agentx');
const STATE_FILE = path.join(STATE_DIR, 'runtime-state.json');

function ensureStateDir(): void {
  if (!existsSync(STATE_DIR)) {
    mkdirSync(STATE_DIR, { recursive: true });
  }
}

function defaultState(): RuntimeStateShape {
  return {
    pendingApproval: null,
    events: [],
    tokenStats: { total: 0, saved: 0, lastTurn: 0 }
  };
}

export class RuntimeStateStore {
  private read(): RuntimeStateShape {
    ensureStateDir();
    if (!existsSync(STATE_FILE)) {
      this.write(defaultState());
      return defaultState();
    }

    try {
      const raw = readFileSync(STATE_FILE, 'utf8');
      const data = JSON.parse(raw);
      const defaultSt = defaultState();
      return {
        ...defaultSt,
        ...data,
        tokenStats: {
          ...defaultSt.tokenStats,
          ...(data.tokenStats || {})
        }
      };
    } catch {
      const state = defaultState();
      this.write(state);
      return state;
    }
  }

  private write(state: RuntimeStateShape): void {
    ensureStateDir();
    writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
  }

  setPendingApproval(approval: RuntimeApproval | null): void {
    const state = this.read();
    state.pendingApproval = approval;
    this.write(state);
  }

  getState(): RuntimeStateShape {
    return this.read();
  }

  addEvent(event: RuntimeEvent): void {
    const state = this.read();
    state.events = [event, ...state.events].slice(0, 50);
    this.write(state);
  }

  addTokenStats(stats: { total: number; saved: number; lastTurn: number }): void {
    const state = this.read();
    state.tokenStats.total += Number(stats.total) || 0;
    state.tokenStats.saved += Number(stats.saved) || 0;
    state.tokenStats.lastTurn = Number(stats.lastTurn) || 0;
    this.write(state);
  }

  clear(): void {
    this.write(defaultState());
  }
}

export function getRuntimeStateFilePath(): string {
  ensureStateDir();
  return STATE_FILE;
}
