import React from 'react';
import { Box, Text } from 'ink';
import { useConfig } from '../contexts/ConfigContext.js';
import { themeManager } from '../themes/theme-manager.js';
import { estimateEtaSeconds } from '../utils/duration.js';
import { formatDuration } from '../utils/toolFormatters.js';
import { ThinkingIndicator } from './ThinkingIndicator.js';
import type { ThinkingStatus } from '../types/thinking.js';
import {
  formatOperationHealth,
  type OperationHealthSnapshot,
} from '../utils/operationHealthFormatting.js';

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
  operationHealth?: OperationHealthSnapshot | null;
  connectionStatus?: 'connected' | 'connecting' | 'error' | 'offline';
  modelProvider?: string;
  deploymentMode?: string;
  errorCount?: number;
  isOperationRunning: boolean;
  isInputPaused: boolean;
  operationName?: string;
  thinkingStatus?: ThinkingStatus;
  animationsEnabled?: boolean;
}

export const Footer: React.FC<FooterProps> = React.memo(({
  model,
  debugMode = false,
  operationMetrics,
  operationHealth,
  connectionStatus = 'connected',
  modelProvider,
  deploymentMode,
  isOperationRunning,
  isInputPaused,
  operationName,
  thinkingStatus,
  animationsEnabled = true,
  errorCount = 0,
}) => {
  const theme = themeManager.getCurrentTheme();
  const { config } = useConfig();

  // --- Footer Rendering (always visible) ---
  const formatCost = (cost: number) => {
    if (cost === 0) return '$0.00';
    return cost < 0.01 ? '<$0.01' : `$${cost.toFixed(2)}`;
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
  const etaSeconds = estimateEtaSeconds(operationMetrics?.duration, progressPercent);
  const hasMem = (operationMetrics?.memoryOps || 0) > 0;
  const healthVisual = formatOperationHealth(operationHealth);
  // Build a single-line footer string and hard-truncate to terminal width to avoid Ink layout bugs
  const cols = Number.isFinite(process.stdout.columns) && process.stdout.columns ? Math.floor(process.stdout.columns) : 80;
  const left = deploymentMode || '';
  const rightParts: string[] = [];
  if (healthVisual) rightParts.push(healthVisual.label);
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

  const healthIndex = healthVisual ? line.indexOf(healthVisual.label) : -1;
  const linePrefix = healthIndex >= 0 ? line.slice(0, healthIndex) : line;
  const visibleHealthLabel = healthIndex >= 0
    ? line.slice(healthIndex, healthIndex + healthVisual!.label.length)
    : '';
  const lineSuffix = healthIndex >= 0 ? line.slice(healthIndex + visibleHealthLabel.length) : '';

  const activeThinking = thinkingStatus?.active === true;

  return (
    <Box flexDirection="column">
      {activeThinking && (
        <ThinkingIndicator
          context={thinkingStatus.context}
          message={thinkingStatus.message}
          startTime={thinkingStatus.startTime}
          taskTitle={thinkingStatus.taskTitle}
          enabled={animationsEnabled}
          maxWidth={cols}
        />
      )}
      <Box>
        <Text color={connIcon.color}>{connIcon.icon}</Text>
        <Text color={theme.muted}>
          {line ? ` ${linePrefix}` : ''}
          {visibleHealthLabel && (
            <Text color={healthVisual!.color} bold>{visibleHealthLabel}</Text>
          )}
          {lineSuffix}
        </Text>
      </Box>
    </Box>
  );
});

Footer.displayName = 'Footer';
