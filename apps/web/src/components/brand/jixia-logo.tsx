import Image from 'next/image';

type JixiaLogoProps = {
  compact?: boolean;
  className?: string;
};

export function JixiaLogo({ compact = false, className = '' }: JixiaLogoProps) {
  return (
    <span className={`jx-brand ${compact ? 'jx-brand--compact' : ''} ${className}`.trim()}>
      <Image
        alt="稷下"
        className="jx-brand__mark"
        height={72}
        priority
        src="/assets/logo-ui.webp"
        width={72}
      />
      <span className="jx-brand__copy">
        <strong>稷下人机交互平台</strong>
      </span>
    </span>
  );
}
