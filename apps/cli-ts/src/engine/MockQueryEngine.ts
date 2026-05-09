import { QueryEngine, ModelDirective } from './QueryEngine.js';
import { ToolManager } from './ToolManager.js';
import fs from 'fs';
import path from 'path';

export class MockQueryEngine extends QueryEngine {
  private playbookPath = path.join(process.cwd(), 'simulation_playbook.json');
  private playbookIndex = 0;

  constructor(toolManager: ToolManager) {
    super(toolManager);
  }

  public override async query(prompt: string): Promise<string> {
    const trimmed = prompt.trim();
    if (!trimmed) return 'Enter a prompt.';

    // RE-ENABLE command handling for Mock mode
    if (this.commands.isCommand(trimmed)) {
      return await this.handleCommand(trimmed);
    }
    
    this.history.push({ role: 'user', content: trimmed });
    
    try {
      return await this.runToolLoop();
    } catch (error) {
      const message = `Mock Agent Error: ${error instanceof Error ? error.message : String(error)}`;
      this.history.push({ role: 'assistant', content: message });
      return message;
    }
  }

  /**
   * Overrides the real LLM request with a mock directive from the playbook
   */
  protected override async requestDirective(): Promise<ModelDirective> {
    let directive: ModelDirective;

    // 1. Check for manual override first
    const manualPath = path.join(process.cwd(), 'manual_brain.json');
    if (fs.existsSync(manualPath)) {
      try {
        directive = JSON.parse(fs.readFileSync(manualPath, 'utf8'));
        fs.unlinkSync(manualPath);
      } catch (e) {
        console.error('Failed to parse manual_brain.json:', e);
        directive = { assistant_message: "Error parsing manual brain." };
      }
    } 
    // 2. Check the simulation playbook
    else if (fs.existsSync(this.playbookPath)) {
      try {
        const playbook = JSON.parse(fs.readFileSync(this.playbookPath, 'utf8'));
        if (Array.isArray(playbook) && playbook[this.playbookIndex]) {
          directive = playbook[this.playbookIndex];
          this.playbookIndex++;
        } else {
          directive = { assistant_message: "Simulation complete or index out of range." };
        }
      } catch (e) {
        console.error('Failed to parse simulation_playbook.json:', e);
        directive = { assistant_message: "Error parsing playbook." };
      }
    }
    // 3. Fallback
    else {
      directive = {
        assistant_message: "Mock Engine: No simulation active. Create 'simulation_playbook.json' to start a scenario.",
      };
    }

    this.reportStats(directive);
    return directive;
  }

  private reportStats(directive: ModelDirective) {
    let saved = 0;
    if (directive.tool_call) {
      if (directive.tool_call.name === 'semantic_search') saved = 1200;
      if (directive.tool_call.name === 'read_snippet') saved = 800;
    }

    this.runtimeState.addTokenStats({
      total: 150,
      saved: saved,
      lastTurn: 150
    });
  }
}
