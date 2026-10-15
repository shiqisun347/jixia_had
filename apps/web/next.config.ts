import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR ?? '.next',
  output: 'standalone',
  images: { unoptimized: true },
  async redirects() {
    return [
      { source: '/experiments', destination: '/lobby', permanent: false },
      { source: '/admin/experiments', destination: '/admin/matches', permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: '/assets/jixia-debate-hero-v5-transparent.webp',
        headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }],
      },
    ];
  },
  async rewrites() {
    const coreOrigin = (process.env.CORE_API_ORIGIN ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
    return [{ source: '/api/:path*', destination: `${coreOrigin}/api/:path*` }];
  },
};

export default nextConfig;
