import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from 'crypto';

const ALGORITHM = 'aes-256-gcm';
const IV_LENGTH = 16;
const SALT_LENGTH = 64;
const KEY_LENGTH = 32;

export class VaultCrypto {
  private key: Buffer;

  constructor(masterPassword: string) {
    // Generate a secure key from the master password
    this.key = scryptSync(masterPassword, 'agentx-salt', KEY_LENGTH);
  }

  encrypt(text: string): { iv: string; content: string; tag: string } {
    const iv = randomBytes(IV_LENGTH);
    const cipher = createCipheriv(ALGORITHM, this.key, iv);
    
    let encrypted = cipher.update(text, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    
    const tag = cipher.getAuthTag().toString('hex');
    
    return {
      iv: iv.toString('hex'),
      content: encrypted,
      tag: tag
    };
  }

  decrypt(encrypted: { iv: string; content: string; tag: string }): string {
    const decipher = createDecipheriv(ALGORITHM, this.key, Buffer.from(encrypted.iv, 'hex'));
    decipher.setAuthTag(Buffer.from(encrypted.tag, 'hex'));
    
    let decrypted = decipher.update(encrypted.content, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    
    return decrypted;
  }
}
