import React, { useState } from 'react';
import { render, Text, Box } from 'ink';
import { QueryEngine } from './engine/QueryEngine.js';
import { MockQueryEngine } from './engine/MockQueryEngine.js';
import { ToolManager } from './engine/ToolManager.js';
import TextInput from 'ink-text-input';
import { StatusBar } from './ui/StatusBar.js';
import { CostDashboard } from './ui/CostDashboard.js';
import type { CostSnapshot } from './providers/costTracker.js';

const App = () => {
  const [query, setQuery] = useState('');
  const [responses, setResponses] = useState<string[]>([]);
  const [status, setStatus] = useState<'idle' | 'thinking' | 'executing'>('idle');
  const [toolManager] = useState(new ToolManager());
  
  const isMock = process.env['AGENTX_MOCK'] === 'true';
  const [engine] = useState(() => isMock ? new MockQueryEngine(toolManager) : new QueryEngine(toolManager));

  const [costSummary, setCostSummary] = useState('');
  const [costSnapshot, setCostSnapshot] = useState<CostSnapshot>(engine.getCostTracker().getSnapshot());

  const handleSubmit = async (value: string) => {
    setQuery('');
    setResponses(prev => [...prev, `> ${value}`]);
    setStatus('thinking');
    const response = await engine.query(value);
    setStatus('idle');
    setResponses(prev => [...prev, response]);
    const tracker = engine.getCostTracker();
    setCostSummary(tracker.formatSavingsSummary());
    setCostSnapshot(tracker.getSnapshot());
  };

  return (
    <Box flexDirection="column" padding={1}>
      <Box borderStyle="round" borderColor="cyan" paddingX={2}>
        <Text bold color="blue">Agentic AI Workflow Engine (AgentX)</Text>
      </Box>
      
      <Box flexDirection="column" marginY={1}>
        {responses.map((resp, i) => (
          <Text key={i} color={resp.startsWith('>') ? 'gray' : 'white'}>
            {resp}
          </Text>
        ))}
      </Box>

      <Box>
        <Text color="green">Prompt: </Text>
        <TextInput value={query} onChange={setQuery} onSubmit={handleSubmit} />
      </Box>

      <Box marginTop={1}>
        <StatusBar status={status} costSummary={costSummary} costMode={engine.getCostMode()} />
      </Box>

      <Box marginTop={1}>
        <CostDashboard snapshot={costSnapshot} mode={engine.getCostMode()} />
      </Box>
    </Box>
  );
};

render(<App />);
