import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { Button } from './button';

test('disabled shared button keeps an explicit readable state', () => {
  render(
    <Button disabled variant="primary">
      保存
    </Button>,
  );
  const button = screen.getByRole('button', { name: '保存' });
  expect(button).toBeDisabled();
  expect(button.className).toContain('disabled:bg-slate-100');
  expect(button.className).toContain('disabled:!text-slate-400');
  expect(button.className).toContain('disabled:opacity-100');
});
