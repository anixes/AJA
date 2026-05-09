import React from 'react';
import { Box, Text } from 'ink';
import type { CostSnapshot } from '../providers/costTracker.js';
import type { CostMode } from '../engine/CostMode.js';

interface CostDashboardProps {
  snapshot: CostSnapshot;
  mode: CostMode;
}

function formatUsd(amount: number): string {
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return `${count}`;
}

function modeLabel(mode: CostMode): string {
  if (mode === 'eco') return 'Eco';
  if (mode === 'premium') return 'Premium';
  return 'Balanced';
}

function modeColor(mode: CostMode): 'green' | 'yellow' | 'magenta' {
  if (mode === 'eco') return 'green';
  if (mode === 'premium') return 'magenta';
  return 'yellow';
}

export const CostDashboard: React.FC<CostDashboardProps> = ({ snapshot, mode }) => {
  const providerSummary = snapshot.providerBreakdown.length
    ? snapshot.providerBreakdown
        .slice(0, 3)
        .map((p) => `${p.provider} ${p.percentage}%`)
        .join(' · ')
    : 'no provider usage yet';

  const completedLine = snapshot.turns > 0
    ? `Completed for ${formatUsd(snapshot.totalCost)} | Saved ${snapshot.savedPercentage}%`
    : 'No model usage yet';

  return (
    <Box borderStyle="single" borderColor="cyan" paddingX={1} flexDirection="column">
      <Box justifyContent="space-between">
        <Text bold color="cyan">Cost Dashboard</Text>
        <Text color={modeColor(mode)}>{modeLabel(mode)} Mode</Text>
      </Box>

      <Box marginTop={1}>
        <Text color="white">Tokens: {formatTokens(snapshot.totalTokens)}</Text>
        <Text color="gray"> | </Text>
        <Text color="white">Cost: {formatUsd(snapshot.totalCost)}</Text>
        <Text color="gray"> | </Text>
        <Text color="green">Saved: {snapshot.savedPercentage}%</Text>
      </Box>

      <Box>
        <Text color="gray">vs premium-only: {formatUsd(snapshot.savedAmount)}</Text>
      </Box>

      <Box>
        <Text color="gray">Providers: {providerSummary}</Text>
      </Box>

      <Box>
        <Text color="cyan">{completedLine}</Text>
      </Box>
    </Box>
  );
};
