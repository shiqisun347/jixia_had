import { afterEach, describe, expect, it, vi } from 'vitest';

import { classifyDeviceProbe, classifyInputLevel, runSpeakerProbe } from './livekit-device-probe';

describe('classifyDeviceProbe', () => {
  it('passes healthy realtime network measurements', () => {
    expect(classifyDeviceProbe(120, 1)).toBe('PASS');
  });

  it('warns for elevated latency or packet loss', () => {
    expect(classifyDeviceProbe(201, 1)).toBe('WARN');
    expect(classifyDeviceProbe(120, 3.1)).toBe('WARN');
  });

  it('fails for unusable latency or packet loss', () => {
    expect(classifyDeviceProbe(401, 1)).toBe('FAIL');
    expect(classifyDeviceProbe(120, 8.1)).toBe('FAIL');
  });

  it('warns when the browser cannot provide complete network measurements', () => {
    expect(classifyDeviceProbe(null, 1)).toBe('WARN');
    expect(classifyDeviceProbe(120, null)).toBe('WARN');
  });
});

describe('classifyInputLevel', () => {
  it('classifies silent, low and healthy microphone input', () => {
    expect(classifyInputLevel(null)).toBe('FAIL');
    expect(classifyInputLevel(0.001)).toBe('FAIL');
    expect(classifyInputLevel(0.005)).toBe('WARN');
    expect(classifyInputLevel(0.02)).toBe('PASS');
  });
});

describe('runSpeakerProbe', () => {
  afterEach(() => {
    delete window.__JX_SPEAKER_PROBE_OVERRIDE__;
  });

  it('uses the localhost-only browser test override', async () => {
    window.__JX_SPEAKER_PROBE_OVERRIDE__ = vi.fn().mockResolvedValue(true);
    const play = vi.fn();

    await expect(runSpeakerProbe(play)).resolves.toBe(true);
    expect(play).not.toHaveBeenCalled();
  });

  it('uses the real speaker probe when no override is present', async () => {
    const play = vi.fn().mockResolvedValue(false);

    await expect(runSpeakerProbe(play)).resolves.toBe(false);
    expect(play).toHaveBeenCalledTimes(1);
  });
});
