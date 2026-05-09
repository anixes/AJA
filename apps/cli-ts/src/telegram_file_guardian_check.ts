import { validateFileOperation } from './engine/FileGuardian.js';

async function main() {
  const filePath = process.argv[2] || '.agentx/telegram-command.txt';
  const content = process.argv[3] || '';
  const decision = await validateFileOperation(filePath, content, process.cwd());
  console.log(JSON.stringify({ decision }));
}

main().catch((error) => {
  console.error(JSON.stringify({ decision: 'DENY', error: String(error?.message || error) }));
  process.exit(1);
});
