/**
 * Thinking Indicator Component
 * Shows animated thinking state between tool calls
 * Optimized using ink-spinner for better performance
 */

import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';
import Spinner from 'ink-spinner';
import { themeManager } from '../themes/theme-manager.js';
import type { ThinkingContext } from '../types/thinking.js';

interface ThinkingIndicatorProps {
  context?: ThinkingContext;
  startTime?: number;
  message?: string;
  toolName?: string;
  toolCategory?: string;
  enabled?: boolean;
  taskTitle?: string | null;
  maxWidth?: number;
}

// Fun thinking phrases that cycle through
const THINKING_PHRASES = [
  'Thinking',
  'Analyzing',
  'Processing',
  'Computing',
  'Hacking away',
  'Exploring paths',
  'Crafting strategy',
  'Brewing ideas',
  'Pondering options',
  'Weighing approaches',
  'Scanning possibilities',
  'Plotting next move',
  'Connecting dots',
  'Crunching data',
  'Running scenarios',
  'Testing theories',
  'Building game plan',
  'Formulating tactics',
  'Calibrating approach',
  'Piecing together',
  'Assembling strategy',
  'Decoding patterns',
  'Spinning up ideas',
  'Cooking up plan'
];

// Context-aware messages
const getContextMessage = (context?: string, phraseIndex?: number): string => {
  switch (context) {
    case 'startup':
      return 'Initializing';
    case 'rate_limit':
        return 'Rate Limit - Waiting';
    case 'reasoning':
    case 'tool_preparation':
    case 'tool_execution':
    case 'waiting':
    default:
      // Cycle through fun phrases for non-startup contexts
      return THINKING_PHRASES[phraseIndex || 0];
  }
};

const truncateText = (value: string, maxWidth?: number): string => {
  if (!maxWidth || maxWidth <= 0 || value.length <= maxWidth) {
    return value;
  }
  if (maxWidth <= 1) {
    return value.slice(0, maxWidth);
  }
  return `${value.slice(0, maxWidth - 1)}…`;
};

export const ThinkingIndicator: React.FC<ThinkingIndicatorProps> = ({
  context,
  startTime,
  message,
  taskTitle,
  maxWidth,
  enabled = true
}) => {
  const theme = themeManager.getCurrentTheme();
  const isRecordingMode = process.env.CYBER_RECORDING_MODE === 'true';
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [phraseIndex, setPhraseIndex] = useState(Math.floor(Math.random() * THINKING_PHRASES.length));

  // Elapsed time tracking (single interval)
  useEffect(() => {
    if (!startTime || !enabled) {
      setElapsedSeconds(0);
      return;
    }

    if (isRecordingMode) {
      setElapsedSeconds(0);
      return;
    }

    const updateElapsed = () => {
      setElapsedSeconds(Math.floor((Date.now() - startTime) / 1000));
    };

    updateElapsed();
    const interval = setInterval(updateElapsed, 1000);

    return () => clearInterval(interval);
  }, [startTime, enabled, isRecordingMode]);

  // Cycle through phrases every 18 seconds (only for non-startup contexts)
  useEffect(() => {
    if (context === 'startup' || !enabled || message) return;

    const interval = setInterval(() => {
      setPhraseIndex(prev => (prev + 1) % THINKING_PHRASES.length);
    }, 18000);

    return () => clearInterval(interval);
  }, [context, enabled, message]);

  // Format elapsed time
  const formatElapsed = (seconds: number): string => {
    if (seconds < 60) {
      return `${seconds}s`;
    }
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${secs}s`;
  };

  const statusSuffix = startTime ? ` [${formatElapsed(elapsedSeconds)}]` : '';
  const displayMessage = (taskTitle ? `${taskTitle} - ` : '') + (message || getContextMessage(context, phraseIndex));
  const spinnerWidth = enabled ? 1 : '[BUSY]'.length;
  const textWidth = maxWidth ? Math.max(0, maxWidth - spinnerWidth - 1) : undefined;
  const displayText = truncateText(`${displayMessage}${statusSuffix}`, textWidth);

  return (
    <Box>
      {enabled ? (
        <Text color={theme.primary}>
          {isRecordingMode ? '⌛' : <Spinner type="dots" />}
        </Text>
      ) : (
        <Text color={theme.muted}>[BUSY]</Text>
      )}
      <Text color={theme.muted}> </Text>
      <Text color={theme.foreground}>{displayText}</Text>
    </Box>
  );
};

// Minimal inline thinking indicator for between events
export const InlineThinking: React.FC<{ message?: string }> = ({ message = 'thinking' }) => {
  const theme = themeManager.getCurrentTheme();
  const [dots, setDots] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => (prev + 1) % 4);
    }, 400);

    return () => clearInterval(interval);
  }, []);

  return (
    <Text color={theme.muted} italic>
      {message}{'.'.repeat(dots)}
    </Text>
  );
};
