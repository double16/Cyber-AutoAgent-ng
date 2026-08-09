/**
 * Event Renderer Test Component
 * 
 * Simple component for testing event rendering in isolation.
 * Uses the actual MemoizedEventLine component from StreamDisplay
 * to ensure tests validate real rendering behavior.
 */

import React from 'react';
import { Box } from 'ink';
import { EventLine as MemoizedEventLine } from '../../src/components/StreamDisplay.tsx';

/**
 * EventRenderer - Test wrapper for event display
 * 
 * @param {Object} props
 * @param {Object} props.event - The event to render
 */
export const EventRenderer = ({event}) => {
  // Simulate the context that would normally be provided by StreamDisplay
  const toolStates = new Map();
  const animationsEnabled = false; // Disable animations for testing
  
  const processedEvent = { ...event };

    // Render the event
  return (
    <Box flexDirection="column">
      <MemoizedEventLine
        event={processedEvent}
        toolStates={toolStates}
        animationsEnabled={animationsEnabled}
      />
    </Box>
  );
};

export default EventRenderer;
