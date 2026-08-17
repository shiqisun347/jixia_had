import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';

import { adminActionItemBaseClass, AdminButton } from './admin-controls';

afterEach(() => cleanup());

test('disabled admin action is visibly distinct from the canvas', () => {
  render(
    <AdminButton disabled tone="danger">
      停用
    </AdminButton>,
  );
  const button = screen.getByRole('button', { name: '停用' });
  expect(button).toBeDisabled();
  expect(button.className).toContain('disabled:bg-slate-100');
  expect(button.className).toContain('disabled:border-slate-200');
  expect(button.className).toContain('disabled:opacity-100');
});

test('disabled admin menu item uses an opaque neutral state', () => {
  expect(adminActionItemBaseClass).toContain('data-[disabled]:bg-slate-100');
  expect(adminActionItemBaseClass).toContain('data-[disabled]:border-slate-200');
  expect(adminActionItemBaseClass).toContain('data-[disabled]:!text-slate-400');
  expect(adminActionItemBaseClass).toContain('data-[disabled]:opacity-100');
});
