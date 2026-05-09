export interface CommandContext {
  cwd: string;
}

export interface CommandResult {
  output: string;
}

export interface CommandDefinition {
  name: string;
  description: string;
  usage: string;
  execute: (args: string, context: CommandContext) => Promise<CommandResult> | CommandResult;
}
