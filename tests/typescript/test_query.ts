import { QueryEngine } from '../../apps/cli-ts/src/engine/QueryEngine.js';
import { ToolManager } from '../../apps/cli-ts/src/engine/ToolManager.js';

async function main() {
  const prompt = process.argv.slice(2).join(' ') || '/help';
  const engine = new QueryEngine(new ToolManager());
  const output = await engine.query(prompt);
  console.log(output);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
