'use client';

import { useQuery } from '@tanstack/react-query';

import { adminApi, readableAdminError } from './admin-api';
import type { Catalog } from './admin-types';

export function useAdminCatalog() {
  const query = useQuery({ queryKey: ['admin', 'catalog'], queryFn: adminApi.catalog });
  return {
    catalog:
      query.data ??
      ({ models: [], agents: [], voices: [], topics: [], rules: [] } satisfies Catalog),
    loading: query.isLoading,
    error: query.error ? readableAdminError(query.error) : '',
    reload: query.refetch,
  };
}
