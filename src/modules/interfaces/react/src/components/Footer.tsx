import React from 'react';
import { Box, Text } from 'ink';
import { useConfig } from '../contexts/ConfigContext.js';
import { themeManager } from '../themes/theme-manager.js';
import { formatDuration } from '../utils/toolFormatters.js';

interface FooterProps {
  model?: string;
  debugMode?: boolean;
  operationMetrics?: {
    tokens?: number;
    inputTokens?: number;
    outputTokens?: number;
    cost?: number;
    duration?: string;
    memoryOps?: number;
    evidence?: number;
    progressPercent?: number;
  };
  connectionStatus?: 'connected' | 'connecting' | 'error' | 'offline';
  modelProvider?: string;
  deploymentMode?: string;
  errorCount?: number;
  isOperationRunning: boolean;
  isInputPaused: boolean;
  operationName?: string;
}

export const Footer: React.FC<FooterProps> = React.memo(({
  model,
  debugMode = false,
  operationMetrics,
  connectionStatus = 'connected',
  modelProvider,
  deploymentMode,
  isOperationRunning,
  isInputPaused,
  operationName,
  errorCount = 0,
}) => {
  const theme = themeManager.getCurrentTheme();
  const { config } = useConfig();

  // --- Footer Rendering (always visible) ---
  const formatCost = (cost: number) => {
    if (cost === 0) return '$0.00';
    return cost < 0.01 ? '<$0.01' : `$${cost.toFixed(2)}`;
  };

  const parseDurationSeconds = (duration: string): number | null => {
    const matches = [...duration.matchAll(/(\d+)\s*([hms])/g)];
    if (matches.length === 0) {
      return null;
    }

    const normalizedDuration = duration.replace(/\s+/g, '');
    const consumedDuration = matches.map((match) => `${match[1]}${match[2]}`).join('');
    if (normalizedDuration !== consumedDuration) {
      return null;
    }

    return matches.reduce((totalSeconds, match) => {
      const value = Number(match[1]);
      const unit = match[2];

      if (unit === 'h') return totalSeconds + (value * 3600);
      if (unit === 'm') return totalSeconds + (value * 60);
      return totalSeconds + value;
    }, 0);
  };

  const getConnectionIcon = () => {
    switch (connectionStatus) {
      case 'connected':
        return { icon: '●', color: theme.success };
      case 'connecting':
        return { icon: '◐', color: theme.warning };
      case 'error':
        return { icon: '✗', color: theme.danger };
      default:
        return { icon: '○', color: theme.muted };
    }
  };

  const connIcon = getConnectionIcon();
  const totalCost = formatCost(operationMetrics?.cost || 0);
  const totalTokens = (operationMetrics?.tokens || 0).toLocaleString();
  const progressPercent = operationMetrics?.progressPercent;
  const hasDuration = !!operationMetrics?.duration && operationMetrics?.duration !== '0s';
  const elapsedSeconds = hasDuration ? parseDurationSeconds(operationMetrics!.duration) : null;
  const etaSeconds = elapsedSeconds !== null
    && progressPercent !== undefined
    && Number.isFinite(progressPercent)
    && progressPercent > 0
    && progressPercent < 100
    ? Math.round((elapsedSeconds / (progressPercent / 100)))
    : null;
  const hasMem = (operationMetrics?.memoryOps || 0) > 0;

  // Build a single-line footer string and hard-truncate to terminal width to avoid Ink layout bugs
  const cols = Number.isFinite(process.stdout.columns) && process.stdout.columns ? Math.floor(process.stdout.columns) : 80;
  const left = deploymentMode || '';
  const rightParts: string[] = [];
  if (model) rightParts.push(model);
  if (progressPercent !== undefined) rightParts.push(`${progressPercent}% budget`);
  rightParts.push(`${totalTokens} tokens`, totalCost);
  if (hasDuration) rightParts.push(operationMetrics!.duration);
  if (etaSeconds !== null && etaSeconds > 0) rightParts.push(`ETA ${formatDuration(etaSeconds, false)}`);
  if (hasMem) rightParts.push(`${operationMetrics!.memoryOps} mem`);
  if (errorCount > 0) rightParts.push(`${errorCount} error${errorCount > 1 ? 's' : ''}`);
  rightParts.push('[ESC] Kill Switch');
  if (debugMode) rightParts.push('[DEBUG MODE]');
  const right = rightParts.join(' | ');

  // Ensure at least one space between left and right; clamp to available columns
  const spacer = ' ';
  let line = left ? `${left}${spacer}${right}` : right;
  const textCols = Math.max(0, cols - connIcon.icon.length - 1);
  if (line.length > textCols) {
    // Prefer keeping the right-side info; trim left if necessary
    const keepRight = Math.min(right.length + 1, Math.max(0, textCols - 1));
    const trimmedLeft = left.slice(0, Math.max(0, textCols - keepRight - 1));
    line = `${trimmedLeft}${spacer}${right}`.slice(0, textCols);
  }

  return (
    <Box>
      <Text color={connIcon.color}>{connIcon.icon}</Text>
      <Text color={theme.muted}>{line ? ` ${line}` : ''}</Text>
    </Box>
  );
});

Footer.displayName = 'Footer';
