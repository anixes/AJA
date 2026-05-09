import React from 'react';
import { Text, Box } from 'ink';
import type { CostMode } from '../engine/CostMode.js';

interface StatusBarProps {
  status: 'idle' | 'thinking' | 'executing';
  costSummary?: string;
  costMode?: CostMode;
}

export const StatusBar: React.FC<StatusBarProps> = ({ status, costSummary = '', costMode = 'balanced' }) => {
  const statusColor = {
    idle: 'green',
    thinking: 'yellow',
    executing: 'blue'
  }[status];

  const modeText = costMode === 'eco' ? 'ECO' : costMode === 'premium' ? 'PREMIUM' : 'BALANCED';
  const modeColor = costMode === 'eco' ? 'green' : costMode === 'premium' ? 'magenta' : 'yellow';

  return (
    <Box borderStyle="single" borderColor="gray" paddingX={1} justifyContent="space-between">
      <Box>
        <Text color="gray">Status: </Text>
        <Text color={statusColor} bold>{status.toUpperCase()}</Text>
        <Text color="gray"> · Mode: </Text>
        <Text color={modeColor} bold>{modeText}</Text>
      </Box>
      <Box>
        {costSummary && <Text color="white">💰 {costSummary}</Text>}
      </Box>
    </Box>
  );
};
