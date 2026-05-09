import { MockQueryEngine } from '../src/engine/MockQueryEngine.js';
import { ToolManager } from '../src/engine/ToolManager.js';
import { RuntimeStateStore } from '../src/services/runtimeState.js';

async function runFullSimulation() {
  console.log('🚀 Starting Full AgentX Security Pressure Test...');
  
  const toolManager = new ToolManager();
  const engine = new MockQueryEngine(toolManager);
  const runtimeState = new RuntimeStateStore();
  
  runtimeState.clear();
  
  // 1. Initial Prompt
  console.log('\n--- Step 1: Initial Prompt ---');
  await engine.query("Start the security test simulation.");
  
  // 2. We know Step 2 is an 'ASK' for 'ls -R .'
  console.log('\n--- Step 2: Unpausing via /approve ---');
  const approvalResult = await engine.query("/approve");
  console.log(`Approval Result: ${approvalResult.slice(0, 100)}...`);
  
  // 3. The engine should automatically proceed to Step 3 in the playbook
  // Step 3 is a 'DENY' for 'edit_file' on 'agentx.json'
  
  // Wait, in QueryEngine.approvePendingTool(), it executes the tool and then returns.
  // It DOES NOT automatically call runToolLoop again.
  // The user has to provide a new prompt or we need to trigger the next turn.
  
  console.log('\n--- Step 3: Triggering Next Turn (DENY Test) ---');
  const denyResult = await engine.query("Continue with the simulation.");
  console.log(`Deny Result: ${denyResult}`);
  
  // 4. Final Verification
  const state = runtimeState.getState();
  console.log('\n--- Final Security Event Log ---');
  state.events.forEach(event => {
    console.log(`[${event.type}] ${event.tool}: ${event.message.slice(0, 100)}`);
  });
}

runFullSimulation();
