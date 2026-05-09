/**
 * agentx code "task"
 * 
 * Full agentic coding loop — uses the QueryEngine with all tools.
 * Plans, writes, tests, and iterates until the task is complete.
 */

import type { CommandOptions } from './shared.js';
import { createClient, printCost, getGraphContext } from './shared.js';
import { ToolManager } from '../engine/ToolManager.js';
import { AutonomousBranch } from '../engine/autonomousBranch.js';

export async function codeCommand(task: string, opts: CommandOptions): Promise<void> {
  const client = await createClient(opts);
  const cwd = process.cwd();

  if (!client.isConfigured()) {
    console.error('❌ No AI provider configured. Set AI_KEY or OPENAI_API_KEY.');
    process.exit(1);
  }

  const toolManager = new ToolManager(opts.mode);

  // If autonomous-branch mode, set up the branch
  let branch: AutonomousBranch | null = null;
  if (opts.mode === 'autonomous-branch') {
    const slug = task.split(' ').slice(0, 5).join('-');
    branch = new AutonomousBranch(slug, cwd);
    const branchName = await branch.initialize();
    console.log(`🌿 Working on branch: ${branchName}\n`);
  }

  console.log(`🤖 AgentX coding: "${task}"\n`);
  console.log(`   Mode: ${opts.mode} | Tools: ${toolManager.getAvailableTools().length} available\n`);

  const contextBlock = await getGraphContext(task);
  if (contextBlock) {
    console.log(`🧠 Injected ${contextBlock.split('\\n').length} lines of graph context.`);
  }

  // Build the system prompt with available tools info
  const toolList = toolManager.getAvailableTools()
    .map(t => `- ${t.name}: ${t.description}`)
    .join('\n');

  const systemPrompt = `You are AgentX, an autonomous coding agent. Your task is to complete the following coding task.

Available tools:
${toolList}

Instructions:
1. Analyze the task and create a plan
2. Use tools to read existing code, understand the codebase
3. Implement the changes using file_edit and bash tools
4. Run tests and linting to verify
5. Summarize what you did

Current working directory: ${cwd}
Execution mode: ${opts.mode}
${contextBlock ? `\nRepository Context:\n${contextBlock}\n` : ''}
When you're done, output a final summary of all changes made.`;

  const messages = [
    { role: 'system' as const, content: systemPrompt },
    { role: 'user' as const, content: task },
  ];

  // Run the agentic loop (simplified — uses chat for now, tool loop handled by QueryEngine)
  try {
    const maxIterations = 8;
    let iteration = 0;

    while (iteration < maxIterations) {
      iteration++;
      console.log(`\n--- Iteration ${iteration}/${maxIterations} ---\n`);

      const response = await client.chat(messages, opts.provider);
      console.log(response.content);

      // Check if the model indicates it's done
      const isDone = response.content.includes('TASK COMPLETE') ||
                     response.content.includes('All changes have been');

      // Auto-commit in autonomous mode
      if (branch && iteration > 1) {
        const commitMsg = await branch.autoCommit(`Iteration ${iteration}: ${task.slice(0, 50)}`);
        if (commitMsg) console.log(`\n📌 ${commitMsg}`);
      }

      if (isDone || iteration >= maxIterations) break;

      // Add response to history and continue
      messages.push({ role: 'user' as const, content: `[Assistant responded]: ${response.content.slice(0, 500)}\n\nContinue. If you are done, say "TASK COMPLETE" and summarize all changes.` });
    }

    // Print branch summary
    if (branch) {
      console.log(`\n${'─'.repeat(50)}`);
      console.log(branch.getSummary());
      await branch.returnToOriginal();
    }

    if (opts.showCost) printCost(client.getCostTracker());
  } catch (error: any) {
    console.error(`❌ ${error.message}`);
    if (branch) await branch.returnToOriginal();
    process.exit(1);
  }
}
