'use client';

import { useQuery } from '@tanstack/react-query';
import { KeyRound, Pencil, Trash2 } from 'lucide-react';
import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';

import {
  AdminActionItem,
  AdminActionMenu,
  AdminButton,
  AdminConfirmDialog,
  AdminDrawer,
  AdminPagination,
  AdminRefreshButton,
  AdminSearch,
  AdminSelect,
  StatusBadge,
} from '@/features/admin/admin-controls';
import { adminApi, readableAdminError } from '@/features/admin/admin-api';
import { AdminDataTable } from '@/features/admin/admin-data-table';
import { AdminEmpty, AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import type { UserRow } from '@/features/admin/admin-types';
import { commitAdminAction } from '@/features/admin/commit-admin-action';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { requestJson } from '@/lib/auth-api';
import { AdminBulkActions } from '@/features/admin/admin-bulk-actions';

export default function AdminUsersPage() {
  const toast = useOptionalToast();
  const { isSubmitting, submit } = useAdminSubmit();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('ALL');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [sort, setSort] = useState('created_at');
  const [page, setPage] = useState(1);
  const [drawerUser, setDrawerUser] = useState<UserRow | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<UserRow | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<UserRow | null>(null);
  const deferredQuery = useDeferredValue(query);
  const params = useMemo(
    () => ({
      page,
      page_size: 25 as const,
      q: deferredQuery,
      status: status === 'ALL' ? '' : status,
      sort,
    }),
    [deferredQuery, page, sort, status],
  );
  const usersQuery = useQuery({
    queryKey: ['admin', 'users', params],
    queryFn: () => adminApi.users(params),
  });
  const users = usersQuery.data?.items ?? [];
  const columns = useMemo<ColumnDef<UserRow>[]>(
    () => [
      {
        id: 'select',
        header: '选择',
        cell: ({ row }) => (
          <input
            aria-label={`选择 ${row.original.real_name}`}
            checked={selectedIds.includes(row.original.id)}
            onChange={() =>
              setSelectedIds((current) =>
                current.includes(row.original.id)
                  ? current.filter((id) => id !== row.original.id)
                  : [...current, row.original.id],
              )
            }
            type="checkbox"
          />
        ),
      },
      {
        accessorKey: 'real_name',
        header: '用户',
        cell: ({ row }) => (
          <div>
            <p className="font-black text-slate-950">{row.original.real_name}</p>
            <p className="text-xs text-slate-600">@{row.original.username}</p>
          </div>
        ),
      },
      {
        id: 'stats',
        header: '参与统计',
        cell: ({ row }) => (
          <span className="text-xs text-slate-600">
            参与 {row.original.match_count} · 完赛 {row.original.finished_count} · 胜{' '}
            {row.original.wins} · {row.original.points} 分
          </span>
        ),
      },
      {
        accessorKey: 'role',
        header: '角色',
        cell: ({ row }) => (
          <span className="text-xs font-bold text-slate-700">
            {row.original.role === 'ADMIN' ? '唯一管理员' : '普通用户'}
          </span>
        ),
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: 'actions',
        header: '操作',
        enableSorting: false,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <AdminActionMenu>
              <AdminActionItem onSelect={() => setDrawerUser(row.original)}>
                <Pencil className="mr-2 size-3.5" aria-hidden="true" />
                编辑资料
              </AdminActionItem>
              {row.original.username.toLowerCase() === 'admin' ? (
                <AdminActionItem onSelect={() => window.location.assign('/change-password')}>
                  <KeyRound className="mr-2 size-3.5" aria-hidden="true" />
                  修改自己的密码
                </AdminActionItem>
              ) : (
                <AdminActionItem onSelect={() => setPasswordTarget(row.original)}>
                  <KeyRound className="mr-2 size-3.5" aria-hidden="true" />
                  修改密码
                </AdminActionItem>
              )}
              {row.original.match_count === 0 && row.original.username.toLowerCase() !== 'admin' ? (
                <AdminActionItem onSelect={() => setDeleteTarget(row.original)} tone="danger">
                  <Trash2 className="mr-2 size-3.5" aria-hidden="true" />
                  删除用户
                </AdminActionItem>
              ) : null}
            </AdminActionMenu>
          </div>
        ),
      },
    ],
    [selectedIds],
  );
  // TanStack Table intentionally exposes stateful callbacks that React Compiler cannot memoize.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data: users, columns, getCoreRowModel: getCoreRowModel() });

  useEffect(() => {
    const url = new URL(window.location.href);
    setQuery(url.searchParams.get('q') ?? '');
    setStatus(url.searchParams.get('status') ?? 'ALL');
    setSort(url.searchParams.get('sort') ?? 'created_at');
    setPage(Number(url.searchParams.get('page') ?? 1) || 1);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (query.trim()) url.searchParams.set('q', query.trim());
    else url.searchParams.delete('q');
    if (status === 'ALL') url.searchParams.delete('status');
    else url.searchParams.set('status', status);
    if (sort === 'created_at') url.searchParams.delete('sort');
    else url.searchParams.set('sort', sort);
    if (page === 1) url.searchParams.delete('page');
    else url.searchParams.set('page', String(page));
    window.history.replaceState(null, '', `${url.pathname}${url.search}`);
  }, [page, query, sort, status]);

  async function saveUser(values: Pick<UserRow, 'real_name' | 'status'>) {
    const target = drawerUser;
    if (!target) return;
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () =>
            requestJson(`/api/admin/users/${target.id}`, {
              method: 'PATCH',
              body: JSON.stringify({
                real_name: values.real_name,
                role: target.role,
                status: values.status,
              }),
            }),
          usersQuery.refetch,
        );
      });
      if (!submitted) return;
      setDrawerUser(null);
      toast?.showToast({ message: `${values.real_name} 已更新。`, tone: 'success' });
      if (refreshResult.value === 'refresh_failed') {
        toast?.showToast({ message: '资料已更新，但用户列表未刷新；请手动刷新。', tone: 'info' });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }

  async function deleteUser(target = deleteTarget) {
    if (!target) return;
    try {
      const refreshResult: { value: 'refreshed' | 'refresh_failed' } = { value: 'refreshed' };
      const submitted = await submit(async () => {
        refreshResult.value = await commitAdminAction(
          () => requestJson(`/api/admin/users/${target.id}`, { method: 'DELETE' }),
          usersQuery.refetch,
        );
      });
      if (!submitted) return;
      setDeleteTarget(null);
      toast?.showToast({ message: `${target.real_name} 已删除。`, tone: 'success' });
      if (refreshResult.value === 'refresh_failed') {
        toast?.showToast({ message: '用户已删除，但用户列表未刷新；请手动刷新。', tone: 'info' });
      }
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }

  async function setPassword(target = passwordTarget, newPassword = '') {
    if (!target) return;
    try {
      const submitted = await submit(async () => {
        await requestJson(`/api/admin/users/${target.id}/password`, {
          method: 'POST',
          body: JSON.stringify({ new_password: newPassword }),
        });
      });
      if (!submitted) return;
      setPasswordTarget(null);
      toast?.showToast({ message: `${target.real_name} 的旧会话已撤销。`, tone: 'success' });
    } catch (error: unknown) {
      toast?.showToast({ message: readableAdminError(error), tone: 'error' });
    }
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={<AdminRefreshButton onRefresh={() => usersQuery.refetch()} />}
        description="查看参与统计，维护真实姓名和状态；有比赛历史的用户只能停用，不能删除。"
        eyebrow="USER OPERATIONS"
        title="用户管理"
      />
      {usersQuery.error ? (
        <AdminFeedback message={readableAdminError(usersQuery.error)} tone="error" />
      ) : null}
      <AdminPanel
        title="用户目录"
        description={`${usersQuery.data?.total ?? 0} 个用户 · 服务端分页`}
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <AdminSearch
            label="搜索真实姓名或用户名"
            onChange={(event) => {
              setPage(1);
              setQuery(event.target.value);
            }}
            placeholder="搜索真实姓名或用户名"
            value={query}
          />
          <AdminSelect
            label="筛选用户状态"
            onChange={(event) => {
              setPage(1);
              setStatus(event.target.value);
            }}
            value={status}
          >
            <option value="ALL">全部状态</option>
            <option value="ACTIVE">启用</option>
            <option value="DISABLED">停用</option>
          </AdminSelect>
          <AdminSelect
            label="用户排序"
            onChange={(event) => {
              setPage(1);
              setSort(event.target.value);
            }}
            value={sort}
          >
            <option value="created_at">注册时间</option>
            <option value="username">用户名</option>
            <option value="real_name">真实姓名</option>
            <option value="status">状态</option>
          </AdminSelect>
        </div>
        {usersQuery.isLoading ? (
          <div
            aria-label="正在加载用户"
            className="h-56 animate-pulse rounded-xl bg-slate-50"
            role="status"
          />
        ) : users.length ? (
          <>
            <AdminBulkActions
              ids={selectedIds}
              onClear={() => setSelectedIds([])}
              onCompleted={() => usersQuery.refetch()}
              resource="user"
            />
            <AdminDataTable
              emptyDescription="没有符合筛选条件的用户。"
              emptyTitle="暂无用户"
              table={table}
            />
          </>
        ) : (
          <AdminEmpty>没有符合筛选条件的用户。</AdminEmpty>
        )}
        {usersQuery.data ? (
          <AdminPagination
            onPageChange={setPage}
            page={usersQuery.data.page}
            total={usersQuery.data.total}
            totalPages={usersQuery.data.total_pages}
          />
        ) : null}
      </AdminPanel>

      <UserDrawer
        key={drawerUser?.id ?? 'closed'}
        loading={isSubmitting}
        user={drawerUser}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setDrawerUser(null);
        }}
        onSave={saveUser}
      />
      <AdminConfirmDialog
        confirmLabel="确认删除"
        description={
          deleteTarget
            ? `将删除 ${deleteTarget.real_name} 的账号和头像；该用户没有比赛历史，操作不可恢复。`
            : ''
        }
        onConfirm={() => void deleteUser(deleteTarget)}
        loading={isSubmitting}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setDeleteTarget(null);
        }}
        open={Boolean(deleteTarget)}
        title="删除用户？"
      />
      <PasswordDrawer
        loading={isSubmitting}
        onOpenChange={(open) => {
          if (!open && !isSubmitting) setPasswordTarget(null);
        }}
        onSave={(password) => setPassword(passwordTarget, password)}
        user={passwordTarget}
      />
    </div>
  );
}

function UserDrawer({
  loading,
  user,
  onOpenChange,
  onSave,
}: {
  loading: boolean;
  user: UserRow | null;
  onOpenChange: (open: boolean) => void;
  onSave: (values: Pick<UserRow, 'real_name' | 'status'>) => Promise<void>;
}) {
  const [realName, setRealName] = useState(user?.real_name ?? '');
  const [status, setStatus] = useState(user?.status ?? 'ACTIVE');
  return (
    <AdminDrawer
      description="用户名和角色不可在此修改；保存后新页面立即使用最新资料。"
      footer={
        <div className="flex justify-end gap-2">
          <AdminButton disabled={loading} onClick={() => onOpenChange(false)} type="button">
            取消
          </AdminButton>
          <AdminButton
            loading={loading}
            onClick={() => void onSave({ real_name: realName.trim(), status })}
            tone="primary"
            type="button"
          >
            保存资料
          </AdminButton>
        </div>
      }
      onOpenChange={(open) => {
        if (open || !loading) onOpenChange(open);
      }}
      open={Boolean(user)}
      title={`编辑用户 · ${user?.real_name ?? ''}`}
    >
      <div className="space-y-5">
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          真实姓名
          <input
            className="admin-field"
            disabled={loading}
            value={realName}
            onChange={(event) => setRealName(event.target.value)}
          />
        </label>
        <label className="grid gap-1.5 text-xs font-bold text-slate-600">
          状态
          <select
            className="admin-field"
            disabled={loading || user?.username.toLowerCase() === 'admin'}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="ACTIVE">启用</option>
            <option value="DISABLED">停用</option>
          </select>
        </label>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          用户名：{user?.username}
          <br />
          角色：{user?.role === 'ADMIN' ? '唯一管理员' : '普通用户'}
        </div>
      </div>
    </AdminDrawer>
  );
}

function PasswordDrawer({
  loading,
  user,
  onOpenChange,
  onSave,
}: {
  loading: boolean;
  user: UserRow | null;
  onOpenChange: (open: boolean) => void;
  onSave: (password: string) => Promise<void>;
}) {
  const [password, setPassword] = useState('');
  return (
    <AdminDrawer
      description="管理员直接设置新密码；保存后目标用户的全部旧会话立即失效。"
      footer={
        <div className="flex justify-end">
          <AdminButton
            disabled={password.length < 8}
            loading={loading}
            onClick={() => void onSave(password)}
            tone="primary"
            type="button"
          >
            保存新密码
          </AdminButton>
        </div>
      }
      onOpenChange={(open) => {
        if (open || !loading) onOpenChange(open);
      }}
      open={Boolean(user)}
      title={`修改密码 · ${user?.real_name ?? ''}`}
    >
      <div className="space-y-4">
        <input
          aria-label="新密码"
          className="admin-field font-mono text-lg"
          disabled={loading}
          minLength={8}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 个字符"
          type="password"
          value={password}
        />
      </div>
    </AdminDrawer>
  );
}
