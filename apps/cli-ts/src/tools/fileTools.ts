import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import path from 'path';
import { z } from 'zod';
import { ToolDefinition, ToolResult } from '../types/tool.js';
import { validateFileOperation } from '../engine/FileGuardian.js';

const editSchema = z.object({
  path: z.string().describe('The absolute or relative path to the file to edit.'),
  content: z.string().describe('The full new content for the file.'),
});

const readSchema = z.object({
  path: z.string().describe('The path to the file to read.'),
});

export const fileEditTool: ToolDefinition<typeof editSchema> = {
  name: 'edit_file',
  description: 'Write or overwrite a file with security validation.',
  inputSchema: editSchema,
  permissionLevel: 'high',
  call: async ({ path: filePath, content }, context) => {
    const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(context.cwd, filePath);
    
    // Security Check
    const decision = await validateFileOperation(absolutePath, content);
    
    if (decision === 'DENY') {
      return {
        output: `Security Alert: Access to ${filePath} is restricted.`,
        isError: true,
        summary: `Blocked edit to sensitive file: ${filePath}`,
        metadata: { path: filePath, decision }
      };
    }

    if (decision === 'ASK' && !context.approvalGranted) {
      return {
        output: `Approval Required: Editing ${filePath} involves potentially risky patterns or large changes.`,
        requiresApproval: true,
        approvalCommand: '/approve',
        summary: `Pending approval for ${filePath}`,
        metadata: { path: filePath, decision, contentLength: content.length }
      };
    }

    try {
      const dir = path.dirname(absolutePath);
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }
      writeFileSync(absolutePath, content, 'utf8');
      return {
        output: `Successfully updated ${filePath}.`,
        summary: `File saved: ${filePath}`,
        metadata: { path: filePath, bytes: content.length }
      };
    } catch (error: any) {
      return {
        output: `Failed to write file: ${error.message}`,
        isError: true
      };
    }
  }
};

export const fileReadTool: ToolDefinition<typeof readSchema> = {
  name: 'read_file',
  description: 'Read the contents of a file.',
  inputSchema: readSchema,
  permissionLevel: 'default',
  call: async ({ path: filePath }, context) => {
    const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(context.cwd, filePath);
    
    // Basic read-only check (could use FileGuardian too)
    if (filePath.includes('.git') || filePath.includes('node_modules')) {
      return { output: 'Error: Path access restricted.', isError: true };
    }

    try {
      if (!existsSync(absolutePath)) {
        return { output: `Error: File not found at ${filePath}`, isError: true };
      }
      const content = readFileSync(absolutePath, 'utf8');
      return {
        output: content,
        summary: `Read ${filePath}`,
        metadata: { path: filePath, size: content.length }
      };
    } catch (error: any) {
      return { output: `Error reading file: ${error.message}`, isError: true };
    }
  }
};
