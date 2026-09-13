'use client';

import Image from 'next/image';
import { useAppLocale } from '@/i18n';

type JixiaLogoProps = {
  compact?: boolean;
  className?: string;
};

export function JixiaLogo({ compact = false, className = '' }: JixiaLogoProps) {
  const { locale } = useAppLocale();
  const english = locale === 'en';
  return (
    <span className={`jx-brand ${compact ? 'jx-brand--compact' : ''} ${className}`.trim()}>
      <Image
        alt={english ? 'JX-Debate' : '稷下'}
        className="jx-brand__mark"
        height={88}
        priority
        src="/assets/logo-new.jpeg"
        width={88}
      />
      <span className="jx-brand__copy">
        <strong>
          {english ? 'JX-Debate' : <>稷下<span className="jx-brand__dot" aria-hidden="true">·</span>争鸣</>}
        </strong>
        <small>{english ? 'Multi-person, multi-agent live voice debate platform' : '多人多智能体实时语音交互平台'}</small>
      </span>
    </span>
  );
}
