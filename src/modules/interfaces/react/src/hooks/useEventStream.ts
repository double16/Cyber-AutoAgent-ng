/**
 * Custom hook for managing event streams
 * Optimized with EventStore for better performance
 */

import React from 'react';
import { DisplayStreamEvent } from '../components/StreamDisplay.js';
import { EVENT_TYPES } from '../constants/config.js';
import { EventStore } from '../utils/EventStore.js';

interface EventStreamState {
  events: DisplayStreamEvent[];
  isThinking: boolean;
  progressPercentage: number;
  reasoningBuffer: string[];
  lastToolName: string | null;
}

interface EventStreamActions {
  addEvent: (event: DisplayStreamEvent) => void;
  clearEvents: () => void;
  processEvent: (event: DisplayStreamEvent) => void;
  flushReasoningBuffer: () => void;
}

export const useEventStream = (
  _initialMaxSteps: number = 100,
  maxEvents: number = 5000
): [EventStreamState, EventStreamActions] => {
  // Use EventStore for efficient event management
  const eventStoreRef = React.useRef(new EventStore(maxEvents));
  const [version, setVersion] = React.useState(0); // Force re-render when events change
  
  const [state, setState] = React.useState<EventStreamState>({
    events: [],
    isThinking: false,
    progressPercentage: 0,
    reasoningBuffer: [],
    lastToolName: null,
  });

  // Get events from store
  const events = React.useMemo(() => {
    return eventStoreRef.current.toArray();
  }, [version]);

  // Update state.events when store changes
  React.useEffect(() => {
    setState(prev => ({ ...prev, events }));
  }, [events]);

  const actions = React.useMemo<EventStreamActions>(() => ({
    addEvent: (event: DisplayStreamEvent) => {
      eventStoreRef.current.append(event);
      setVersion(v => v + 1);
    },

    clearEvents: () => {
      eventStoreRef.current.clear();
      setVersion(v => v + 1);
      setState(prev => ({
        ...prev,
        events: [],
        progressPercentage: 0,
        reasoningBuffer: [],
        lastToolName: null,
      }));
    },

    processEvent: (event: DisplayStreamEvent) => {
      setState(prev => {
        const newState = { ...prev };

        switch (event.type) {
          case EVENT_TYPES.PROGRESS_UPDATE:
            if ('progressPercent' in event && typeof event.progressPercent === 'number') {
              newState.progressPercentage = event.progressPercent;
            }
            break;

          case EVENT_TYPES.THINKING:
            newState.isThinking = true;
            break;

          case EVENT_TYPES.THINKING_END:
            newState.isThinking = false;
            break;

          case EVENT_TYPES.TOOL_START:
            if ('tool_name' in event && typeof event.tool_name === 'string') {
              newState.lastToolName = event.tool_name;
            }
            break;

          case EVENT_TYPES.REASONING:
            if ('content' in event && typeof event.content === 'string') {
              newState.reasoningBuffer.push(event.content);
            }
            break;
        }

        // Don't add event here - add to store after state update
        return newState;
      });
      
      // Add event to store after state update
      eventStoreRef.current.append(event);
      setVersion(v => v + 1);
    },

    flushReasoningBuffer: () => {
      setState(prev => {
        if (prev.reasoningBuffer.length === 0) return prev;

        const reasoningEvent: DisplayStreamEvent = {
          type: EVENT_TYPES.REASONING,
          content: prev.reasoningBuffer.join(''),
        };

        eventStoreRef.current.append(reasoningEvent);
        setVersion(v => v + 1);
        
        return {
          ...prev,
          events: eventStoreRef.current.toArray(),
          reasoningBuffer: [],
        };
      });
    },
  }), [version]); // Include version to ensure actions see latest store state

  return [state, actions];
};

/**
 * Hook for grouping events for optimized rendering
 */
export const useEventGroups = (events: DisplayStreamEvent[]) => {
  return React.useMemo(() => {
    const groups: Array<{
      type: 'reasoning_group' | 'single';
      events: DisplayStreamEvent[];
      startIdx: number;
    }> = [];

    let currentReasoningGroup: DisplayStreamEvent[] = [];
    let groupStartIdx = 0;

    events.forEach((event, idx) => {
      if (event.type === EVENT_TYPES.REASONING) {
        if (currentReasoningGroup.length === 0) {
          groupStartIdx = idx;
        }
        currentReasoningGroup.push(event);
      } else {
        // End current reasoning group if exists
        if (currentReasoningGroup.length > 0) {
          groups.push({
            type: 'reasoning_group',
            events: currentReasoningGroup,
            startIdx: groupStartIdx,
          });
          currentReasoningGroup = [];
        }

        // Add non-reasoning event as single
        groups.push({
          type: 'single',
          events: [event],
          startIdx: idx,
        });
      }
    });

    // Handle any remaining reasoning group
    if (currentReasoningGroup.length > 0) {
      groups.push({
        type: 'reasoning_group',
        events: currentReasoningGroup,
        startIdx: groupStartIdx,
      });
    }

    return groups;
  }, [events]);
};
