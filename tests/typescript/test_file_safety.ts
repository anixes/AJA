import { ToolManager } from './engine/ToolManager.js';
import path from 'path';

async function testFileSafety() {
  const manager = new ToolManager();
  const context = {
    cwd: process.cwd(),
    abortSignal: AbortSignal.timeout(30_000),
    sessionId: 'test-file-tools',
    approvalGranted: false
  };

  console.log("--- TEST 1: Restricted File Block (Deny) ---");
  const denyResult = await manager.executeTool('edit_file', { 
    path: 'agentx.json', 
    content: '{"hacked": true}' 
  }, context);
  console.log("File: agentx.json");
  console.log("Decision:", denyResult.isError ? "DENIED (Correct)" : "ALLOWED (Incorrect)");
  console.log("Message:", denyResult.output);

  console.log("\n--- TEST 2: System Pattern Detection (Ask) ---");
  const askResult = await manager.executeTool('edit_file', { 
    path: 'scripts/test_script.py', 
    content: 'import os\nos.system("rm -rf /")\nprocess.exit(1)' 
  }, context);
  console.log("File: scripts/test_script.py (with risky code)");
  console.log("Decision:", askResult.requiresApproval ? "ASK (Correct)" : "INSTANT (Incorrect)");
  
  console.log("\n--- TEST 3: Safe File Read ---");
  const readResult = await manager.executeTool('read_file', { path: 'README.md' }, context);
  console.log("File: README.md");
  console.log("Status:", readResult.isError ? "FAILED" : "SUCCESS");
  console.log("Content Preview:", readResult.output.split('\n')[0]);
}

testFileSafety().catch(console.error);
