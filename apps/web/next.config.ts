import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR ?? '.next',
  output: 'standalone',
  images: { unoptimized: true },
  async rewrites() {
    const coreOrigin = (process.env.CORE_API_ORIGIN ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
    return [{ source: '/api/:path*', destination: `${coreOrigin}/api/:path*` }];
  },
};

export default nextConfig;
