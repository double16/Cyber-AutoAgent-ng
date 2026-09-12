import React from 'react';

// Generic mock for all ink-* components; provide ESM default export
const MockComponent = ({ children, ...props }) => {
  if (Object.prototype.hasOwnProperty.call(props, 'value') && typeof props.onChange === 'function') {
    return React.createElement('input', {
      value: props.value,
      onChange: (event) => props.onChange(event.target.value),
    });
  }
  const content = children || props.text || props.placeholder || '';
  return React.createElement('MockComponent', props, content);
};

export default MockComponent;
