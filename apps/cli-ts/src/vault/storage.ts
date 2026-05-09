import { readFileSync, writeFileSync, existsSync } from 'fs';
import path from 'path';

export interface VaultData {
  [key: string]: {
    iv: string;
    content: string;
    tag: string;
  }
}

export class VaultStorage {
  private filePath: string;

  constructor() {
    this.filePath = path.join(process.cwd(), 'vault_data.json');
    if (!existsSync(this.filePath)) {
      writeFileSync(this.filePath, JSON.stringify({}));
    }
  }

  save(data: VaultData) {
    writeFileSync(this.filePath, JSON.stringify(data, null, 2));
  }

  load(): VaultData {
    return JSON.parse(readFileSync(this.filePath, 'utf-8'));
  }
}
