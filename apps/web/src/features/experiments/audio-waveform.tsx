'use client';

import { LoaderCircle, Pause, Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import WaveSurfer from 'wavesurfer.js';

import { Button } from '@/components/ui/button';

export function AudioWaveform({
  url,
  onPlay,
  className = 'mt-4',
}: Readonly<{ url: string; onPlay?: () => void; className?: string }>) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onPlayRef = useRef(onPlay);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const waveRef = useRef<WaveSurfer | null>(null);

  useEffect(() => {
    onPlayRef.current = onPlay;
  }, [onPlay]);

  useEffect(() => {
    if (!containerRef.current) return;
    const wave = WaveSurfer.create({
      container: containerRef.current,
      url,
      height: 56,
      waveColor: '#94a3b8',
      progressColor: '#2563eb',
      cursorColor: '#0f172a',
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      normalize: true,
    });
    waveRef.current = wave;
    wave.on('ready', () => setReady(true));
    wave.on('play', () => {
      setPlaying(true);
      onPlayRef.current?.();
    });
    wave.on('pause', () => setPlaying(false));
    wave.on('finish', () => setPlaying(false));
    wave.on('error', () => setFailed(true));
    return () => {
      waveRef.current = null;
      wave.destroy();
    };
  }, [url]);

  return (
    <div
      className={`${className} grid min-h-20 grid-cols-[2.75rem_minmax(0,1fr)] items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3`}
    >
      <Button
        aria-label={playing ? '暂停发言音频' : '播放发言音频'}
        disabled={!ready || failed}
        onClick={() => waveRef.current?.playPause()}
        size="icon"
        title={playing ? '暂停' : '播放'}
        variant="secondary"
      >
        {!ready && !failed ? (
          <LoaderCircle className="size-4 animate-spin" />
        ) : playing ? (
          <Pause className="size-4" />
        ) : (
          <Play className="size-4" />
        )}
      </Button>
      <div className="h-14 min-w-0 overflow-hidden" ref={containerRef}>
        {failed ? (
          <p className="pt-4 text-sm text-red-700">音频暂时无法加载，请刷新后重试。</p>
        ) : null}
      </div>
    </div>
  );
}
