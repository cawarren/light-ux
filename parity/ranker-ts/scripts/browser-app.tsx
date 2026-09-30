// Minimal stock cmdk 1.1.1 page for the browser oracle: default props, no groups, no keywords,
// uncontrolled input. window.__mount(items) renders a fresh <Command> tree.
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Command } from 'cmdk';

let root: Root | null = null;
(window as any).__mount = (items: string[]) => {
  root?.unmount();
  document.body.innerHTML = '<div id="app"></div>';
  root = createRoot(document.getElementById('app')!);
  root.render(
    <Command label="oracle">
      <Command.Input />
      <Command.List>
        <Command.Empty>No results</Command.Empty>
        {items.map((t, i) => (
          <Command.Item key={i} value={t} data-id={i}>{t}</Command.Item>
        ))}
      </Command.List>
    </Command>,
  );
};
