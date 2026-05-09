import { z } from 'zod';
import { ToolDefinition } from '../types/tool.js';
import { VaultCrypto } from '../vault/crypto.js';
import { VaultStorage } from '../vault/storage.js';

const storage = new VaultStorage();

export const vaultTool: ToolDefinition<any> = {
  name: 'vault',
  description: 'Manage secure secrets in the AgentX vault. Actions: add, get, list.',
  inputSchema: z.object({
    action: z.enum(['add', 'get', 'list']).describe('Action to perform'),
    key: z.string().optional().describe('Secret name'),
    value: z.string().optional().describe('Secret value (for add)'),
    password: z.string().describe('Master password for the vault')
  }),
  permissionLevel: 'high',
  call: async ({ action, key, value, password }) => {
    const crypto = new VaultCrypto(password);
    const data = storage.load();

    try {
      if (action === 'add') {
        if (!key || !value) return { output: "Error: Key and Value required for 'add'", isError: true };
        data[key] = crypto.encrypt(value);
        storage.save(data);
        return { output: `Secret '${key}' added successfully.` };
      }

      if (action === 'get') {
        if (!key) return { output: "Error: Key required for 'get'", isError: true };
        if (!data[key]) return { output: `Error: Secret '${key}' not found.`, isError: true };
        const decrypted = crypto.decrypt(data[key]);
        return { output: `Secret '${key}': ${decrypted}` };
      }

      if (action === 'list') {
        return { output: `Available Secrets: ${Object.keys(data).join(', ') || 'None'}` };
      }

      return { output: "Invalid action", isError: true };
    } catch (e) {
      return { output: "Decryption failed: Incorrect password or corrupted data.", isError: true };
    }
  }
};
