/**
 * ExtendedTextInput Component
 *
 * Enhanced text input component for Ink framework with extended capabilities
 * beyond standard ink-text-input limitations. Provides full-length text support,
 * cursor management, and clipboard handling for terminal interfaces.
 *
 * Uses reducer-based state management inspired by gemini-cli for atomic operations
 * and reliable paste handling.
 */

import React, { useEffect } from 'react';
import { Text, useInput } from 'ink';
import { useTextBuffer } from '../hooks/useTextBuffer.js';

interface ExtendedTextInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (value: string) => void;
  placeholder?: string;
  focus?: boolean;
  showCursor?: boolean;
  cursorChar?: string;
  disabled?: boolean;
  textColor?: string;
}

/**
 * ExtendedTextInput provides enhanced text input capabilities for terminal UIs.
 * Supports full-length text input, cursor positioning, and standard text editing
 * operations without the character limitations of standard components.
 */
export const ExtendedTextInput: React.FC<ExtendedTextInputProps> = ({
  value = '',
  onChange,
  onSubmit,
  placeholder = '',
  focus = true,
  showCursor = true,
  cursorChar = '█',
  disabled = false,
  textColor
}) => {
  // Use reducer-based text buffer for atomic operations
  const buffer = useTextBuffer({
    initialValue: value,
    onChange
  });

  // Sync buffer with external value changes
  useEffect(() => {
    if (buffer.text !== value) {
      buffer.setText(value, Math.min(buffer.cursorPosition, value.length));
    }
  }, [value]);

  useInput((input, key) => {
    try {
      if (!focus || disabled) return;

      // Handle form submission first
      if (key.return) {
        if (onSubmit) {
          onSubmit(buffer.text);
        }
        return;
      }

      // Cursor movement - left arrow
      if (key.leftArrow) {
        buffer.moveLeft();
        return;
      }

      // Cursor movement - right arrow
      if (key.rightArrow) {
        buffer.moveRight();
        return;
      }

      // Readline-style cursor movement.
      if ((key.ctrl && input === 'a') || key.home) {
        buffer.moveToStart();
        return;
      }

      if ((key.ctrl && input === 'e') || key.end) {
        buffer.moveToEnd();
        return;
      }

      if (key.ctrl && input === 'b') {
        buffer.moveLeft();
        return;
      }

      if (key.ctrl && input === 'f') {
        buffer.moveRight();
        return;
      }

      // Backspace - delete before cursor
      if (key.backspace || (key.ctrl && input === 'h')) {
        buffer.deleteBeforeCursor();
        return;
      }

      // Delete key - delete at cursor
      if (key.delete || (key.ctrl && input === 'd')) {
        buffer.deleteAfterCursor();
        return;
      }

      // Readline-style line editing. Ctrl+C and Ctrl+L are handled at the App level.
      if (key.ctrl && input === 'u') {
        buffer.deleteToStart();
        return;
      }

      if (key.ctrl && input === 'k') {
        buffer.deleteToEnd();
        return;
      }

      if (key.ctrl && input === 'w') {
        buffer.deleteWordBeforeCursor();
        return;
      }

      // Character insertion at cursor position
      // This handles both normal typing AND paste
      // The reducer ensures atomic updates without race conditions
      if (input && !key.ctrl && !key.meta) {
        buffer.insert(input);
        return;
      }
    } catch (error) {
      // Swallow input errors to avoid crashing input handling
    }
  }, { isActive: focus && !disabled });

  // Render with cursor at correct position
  try {
    if (!buffer.text && placeholder) {
      return <Text color="gray">{placeholder}</Text>;
    }

    // Ensure value is a safe string for rendering
    const safeValue = String(buffer.text || '');

    if (!showCursor || !focus || disabled) {
      return <Text color={textColor}>{safeValue}</Text>;
    }

    // Split text at cursor position and insert cursor character
    const beforeCursor = safeValue.slice(0, buffer.cursorPosition);
    const atCursor = safeValue.slice(buffer.cursorPosition, buffer.cursorPosition + 1) || ' ';
    const afterCursor = safeValue.slice(buffer.cursorPosition + 1);

    // Render with cursor at position
    if (buffer.cursorPosition >= safeValue.length) {
      // Cursor at end
      return <Text color={textColor}>{safeValue}{cursorChar}</Text>;
    } else {
      // Cursor in middle - show inverse character
      return <Text color={textColor}>{beforeCursor}<Text inverse>{atCursor}</Text>{afterCursor}</Text>;
    }
  } catch (error) {
    // Fallback render without logging
    return <Text>{'_'}</Text>;
  }
};
