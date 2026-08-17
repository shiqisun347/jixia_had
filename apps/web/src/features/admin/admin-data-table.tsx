'use client';

import { ChevronDown, ChevronUp, ChevronsUpDown, Inbox } from 'lucide-react';
import type { ReactNode } from 'react';
import { flexRender, type Header, type Table } from '@tanstack/react-table';

import { cn } from '@/lib/cn';

export function AdminDataTable<TData>({
  table,
  emptyTitle = '暂无数据',
  emptyDescription,
  className,
}: {
  table: Table<TData>;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}) {
  const rows = table.getRowModel().rows;
  return (
    <div className={cn('overflow-hidden rounded-2xl border border-slate-200 bg-white', className)}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left">
          <thead className="border-b border-slate-200 bg-slate-50/90">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    className="h-11 px-4 text-[0.68rem] font-black tracking-[0.08em] text-slate-500 uppercase"
                    colSpan={header.colSpan}
                    key={header.id}
                    scope="col"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <tr className="transition-colors hover:bg-blue-50/35" key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td className="px-4 py-3.5 text-sm text-slate-700" key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length ? (
        <div className="grid min-h-56 place-items-center border-t border-slate-100 bg-slate-50/35 px-6 text-center">
          <div>
            <span className="mx-auto grid size-10 place-items-center rounded-xl border border-slate-200 bg-white text-slate-400">
              <Inbox className="size-5" aria-hidden="true" />
            </span>
            <p className="mt-3 text-sm font-black text-slate-800">{emptyTitle}</p>
            {emptyDescription ? (
              <p className="mt-1 text-xs leading-5 text-slate-500">{emptyDescription}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function AdminSortableHeader<TData>({
  header,
  children,
}: {
  header: Header<TData, unknown>;
  children: ReactNode;
}) {
  const sorted = header.column.getIsSorted();
  if (!header.column.getCanSort()) return children;
  const Icon = sorted === 'asc' ? ChevronUp : sorted === 'desc' ? ChevronDown : ChevronsUpDown;
  return (
    <button
      className="-ml-2 inline-flex min-h-8 items-center gap-1 rounded-lg px-2 text-left transition hover:bg-slate-200/70 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
      onClick={header.column.getToggleSortingHandler()}
      type="button"
    >
      {children}
      <Icon className="size-3.5" aria-hidden="true" />
    </button>
  );
}

export function AdminTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div
      aria-label="正在加载数据"
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
      role="status"
    >
      <div className="h-11 border-b border-slate-200 bg-slate-50" />
      {Array.from({ length: rows }, (_, index) => (
        <div className="grid grid-cols-4 gap-6 border-b border-slate-100 px-4 py-4" key={index}>
          <span className="h-3 animate-pulse rounded bg-slate-100" />
          <span className="h-3 animate-pulse rounded bg-slate-100" />
          <span className="h-3 animate-pulse rounded bg-slate-100" />
          <span className="h-3 animate-pulse rounded bg-slate-100" />
        </div>
      ))}
    </div>
  );
}
