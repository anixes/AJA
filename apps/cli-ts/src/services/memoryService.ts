export type SecretaryTaskStatus = 'pending' | 'active' | 'blocked' | 'completed' | 'archived';
export type SecretaryTaskPriority = 'low' | 'medium' | 'high' | 'urgent';
export type SecretaryTaskSource = 'Telegram' | 'CLI' | 'dashboard' | 'system' | string;

export interface SecretaryTask {
  task_id: string;
  title: string;
  context: string;
  owner: string;
  due_date: string | null;
  recurrence: string | null;
  priority: SecretaryTaskPriority;
  status: SecretaryTaskStatus;
  follow_up_state: Record<string, unknown>;
  reminder_state: Record<string, unknown>;
  escalation_level: number;
  approval_required: boolean;
  approval_status: 'not_required' | 'pending' | 'approved' | 'rejected';
  related_people: string[];
  communication_history: Array<Record<string, unknown>>;
  source: SecretaryTaskSource;
  last_reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SecretaryReview {
  overdue: SecretaryTask[];
  due_soon: SecretaryTask[];
  stale: SecretaryTask[];
  blocked: SecretaryTask[];
  active_count: number;
  reviewed_at: string;
}

export interface CommunicationMessage {
  message_id: string;
  recipient: string;
  channel: 'telegram' | 'email' | 'draft' | 'other';
  subject: string;
  draft_content: string;
  tone_profile: string;
  approval_required: boolean;
  approval_status: 'not_required' | 'pending' | 'approved' | 'rejected';
  follow_up_required: boolean;
  follow_up_due: string | null;
  related_task_id: string | null;
  communication_history: Array<Record<string, unknown>>;
  delivery_status: 'draft' | 'ready' | 'sent' | 'failed' | 'cancelled';
  last_sent_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutiveReview {
  kind: 'morning' | 'night' | 'weekly';
  summary: string;
  sections: Record<string, Array<Record<string, unknown>>>;
  generated_at: string;
}

export class MemoryService {
  constructor(
    private readonly apiBase = 'http://localhost:8000',
    private readonly token = process.env.AGENTX_API_TOKEN || 'dev-token-123',
  ) {}

  async listTasks(status?: SecretaryTaskStatus[]): Promise<SecretaryTask[]> {
    const params = status?.length ? `?status=${encodeURIComponent(status.join(','))}` : '';
    const payload = await this.request<{ tasks: SecretaryTask[] }>(`/memory/tasks${params}`);
    return payload.tasks;
  }

  async createTask(input: Partial<SecretaryTask> & { title: string }): Promise<SecretaryTask> {
    const payload = await this.request<{ task: SecretaryTask }>('/memory/tasks', {
      method: 'POST',
      body: JSON.stringify(input),
    });
    return payload.task;
  }

  async completeTask(taskId: string, note = ''): Promise<SecretaryTask> {
    const payload = await this.request<{ task: SecretaryTask }>(`/memory/tasks/${taskId}/complete`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    });
    return payload.task;
  }

  async reviewTasks(escalate = true): Promise<SecretaryReview> {
    const payload = await this.request<{ review: SecretaryReview }>(`/memory/review?escalate=${String(escalate)}`);
    return payload.review;
  }

  async listCommunications(): Promise<CommunicationMessage[]> {
    const payload = await this.request<{ messages: CommunicationMessage[] }>('/communications');
    return payload.messages;
  }

  async createCommunication(input: Partial<CommunicationMessage> & { recipient: string; draft_content: string }): Promise<CommunicationMessage> {
    const payload = await this.request<{ message: CommunicationMessage }>('/communications', {
      method: 'POST',
      body: JSON.stringify(input),
    });
    return payload.message;
  }

  async approveCommunication(messageId: string): Promise<CommunicationMessage> {
    const payload = await this.request<{ message: CommunicationMessage }>(`/communications/${messageId}/approve`, {
      method: 'POST',
    });
    return payload.message;
  }

  async sendCommunication(messageId: string): Promise<{ ok: boolean; message: string }> {
    return await this.request<{ ok: boolean; message: string }>(`/communications/${messageId}/send`, {
      method: 'POST',
    });
  }

  async getExecutiveReview(kind: 'morning' | 'night' | 'weekly', escalate = true): Promise<ExecutiveReview> {
    const payload = await this.request<{ review: ExecutiveReview }>(`/scheduler/review/${kind}?escalate=${String(escalate)}`);
    return payload.review;
  }

  async snoozeTask(taskId: string, until = 'tomorrow', reason = ''): Promise<SecretaryTask> {
    const payload = await this.request<{ task: SecretaryTask }>(`/scheduler/snooze/${taskId}`, {
      method: 'POST',
      body: JSON.stringify({ until, reason }),
    });
    return payload.task;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.apiBase}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.token}`,
        ...(init.headers || {}),
      },
    });

    if (!response.ok) {
      throw new Error(`Secretary memory request failed: ${response.status} ${response.statusText}`);
    }

    return (await response.json()) as T;
  }
}
