import { ToolManager } from './engine/ToolManager.js';
import { RuntimeEvent, RuntimeStateStore } from './services/runtimeState.js';
import { appendFileSync, mkdirSync } from 'fs';
import path from 'path';

function appendApprovalAudit(record: Record<string, unknown>): void {
  const dir = path.join(process.cwd(), '.agentx');
  mkdirSync(dir, { recursive: true });
  appendFileSync(
    path.join(dir, 'approval-audit.jsonl'),
    `${JSON.stringify({ createdAt: new Date().toISOString(), ...record })}\n`,
  );
}

function isExpired(expiresAt?: string): boolean {
  return Boolean(expiresAt && Date.parse(expiresAt) <= Date.now());
}

async function main() {
  const action = process.argv[2];
  if (action !== 'approve' && action !== 'deny') {
    console.error('Usage: npx tsx src/runtime_actions.ts <approve|deny>');
    process.exit(1);
  }

  const store = new RuntimeStateStore();
  const state = store.getState();
  const pending = state.pendingApproval;

  if (!pending) {
    console.log(JSON.stringify({ ok: false, message: 'There is no pending approval.' }));
    return;
  }

  if (isExpired(pending.expiresAt)) {
    store.setPendingApproval(null);
    appendApprovalAudit({ id: pending.id, action: 'expired', requesterSource: pending.requesterSource, command: pending.command });
    store.addEvent({
      id: `event-${Date.now()}`,
      type: 'DENIED',
      tool: pending.tool,
      message: `Approval expired for ${pending.tool}.`,
      command: pending.command,
      rootBinary: pending.rootBinary,
      level: pending.level,
      createdAt: new Date().toISOString(),
    });
    console.log(JSON.stringify({ ok: false, message: 'Approval expired. Request it again.' }));
    return;
  }

  if (action === 'deny') {
    store.setPendingApproval(null);
    appendApprovalAudit({ id: pending.id, action: 'rejected', requesterSource: pending.requesterSource, command: pending.command });
    store.addEvent({
      id: `event-${Date.now()}`,
      type: 'DENIED',
      tool: pending.tool,
      message: `Dashboard denied execution for ${pending.tool}.`,
      command: pending.command,
      rootBinary: pending.rootBinary,
      level: pending.level,
      createdAt: new Date().toISOString(),
    });

    console.log(JSON.stringify({ ok: true, message: `Denied ${pending.tool}.` }));
    return;
  }

  const manager = new ToolManager();
  appendApprovalAudit({ id: pending.id, action: 'approved', requesterSource: pending.requesterSource, command: pending.command });
  const result = await manager.executeTool(pending.tool, pending.input, {
    cwd: process.cwd(),
    abortSignal: AbortSignal.timeout(30_000),
    sessionId: `dashboard-${Date.now()}`,
    approvalGranted: true,
  });

  store.setPendingApproval(null);
  store.addEvent({
    id: `event-${Date.now()}`,
    type: result.isError ? 'DENY' : 'APPROVED',
    tool: pending.tool,
    message: String(result.summary || result.output),
    command: pending.command,
    rootBinary: pending.rootBinary,
    level: pending.level,
    createdAt: new Date().toISOString(),
  });

  appendApprovalAudit({
    id: pending.id,
    action: result.isError ? 'execution_failed' : 'executed',
    requesterSource: pending.requesterSource,
    command: pending.command,
    message: String(result.summary || result.output).slice(0, 1000),
  });

  console.log(
    JSON.stringify({
      ok: !result.isError,
      message: String(result.output),
    }),
  );
}

main().catch((error) => {
  console.error(
    JSON.stringify({
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    }),
  );
  process.exit(1);
});
