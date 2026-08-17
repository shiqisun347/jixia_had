import { roomsApi } from './rooms-api';

export type DeviceProbeStatus = 'PASS' | 'WARN' | 'FAIL';

export interface LiveKitDeviceProbeResult {
  status: DeviceProbeStatus;
  rttP95Ms: number | null;
  packetLossP95Percent: number | null;
  connectionQuality: string;
  samples: number;
  inputPeak: number | null;
  recordingBlob?: Blob;
}

declare global {
  interface Window {
    __JX_DEVICE_PROBE_OVERRIDE__?: () => Promise<LiveKitDeviceProbeResult>;
    __JX_SPEAKER_PROBE_OVERRIDE__?: () => Promise<boolean>;
  }
}

function percentile95(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = values.toSorted((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)] ?? null;
}

export function classifyDeviceProbe(
  rttP95Ms: number | null,
  lossP95Percent: number | null,
): DeviceProbeStatus {
  if (rttP95Ms !== null && rttP95Ms > 400) return 'FAIL';
  if (lossP95Percent !== null && lossP95Percent > 8) return 'FAIL';
  if (rttP95Ms === null || lossP95Percent === null) return 'WARN';
  if (rttP95Ms > 200 || lossP95Percent > 3) return 'WARN';
  return 'PASS';
}

export function classifyInputLevel(inputPeak: number | null): DeviceProbeStatus {
  if (inputPeak === null || inputPeak < 0.002) return 'FAIL';
  if (inputPeak < 0.01) return 'WARN';
  return 'PASS';
}

function combineStatus(...statuses: DeviceProbeStatus[]): DeviceProbeStatus {
  if (statuses.includes('FAIL')) return 'FAIL';
  if (statuses.includes('WARN')) return 'WARN';
  return 'PASS';
}

function throwIfAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('设备检测已取消', 'AbortError');
}

async function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  await new Promise<void>((resolve, reject) => {
    let timer = 0;
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('设备检测已取消', 'AbortError'));
    };
    const finish = () => {
      signal?.removeEventListener('abort', abort);
      resolve();
    };
    timer = window.setTimeout(finish, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

async function captureMicrophoneSample(
  mediaStreamTrack: MediaStreamTrack,
  signal?: AbortSignal,
): Promise<{ inputPeak: number; recordingBlob: Blob }> {
  if (typeof MediaRecorder === 'undefined') {
    throw new Error('media_recorder_unavailable');
  }
  const stream = new MediaStream([mediaStreamTrack]);
  const preferredMimeType = 'audio/webm;codecs=opus';
  const recorder = MediaRecorder.isTypeSupported(preferredMimeType)
    ? new MediaRecorder(stream, { mimeType: preferredMimeType })
    : new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.addEventListener('dataavailable', (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  const stopped = new Promise<void>((resolve, reject) => {
    recorder.addEventListener('stop', () => resolve(), { once: true });
    recorder.addEventListener('error', () => reject(new Error('media_recorder_failed')), {
      once: true,
    });
  });

  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  let inputPeak = 0;
  recorder.start(250);
  try {
    for (let index = 0; index < 30; index += 1) {
      throwIfAborted(signal);
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      inputPeak = Math.max(inputPeak, Math.sqrt(sum / samples.length));
      await delay(100, signal);
    }
  } finally {
    if (recorder.state !== 'inactive') recorder.stop();
    source.disconnect();
    await context.close();
  }
  await stopped;
  const recordingBlob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
  if (!recordingBlob.size) throw new Error('microphone_recording_empty');
  return { inputPeak, recordingBlob };
}

function canUseLocalOverride(): boolean {
  return window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
}

export async function runSpeakerProbe(play: () => Promise<boolean>): Promise<boolean> {
  if (canUseLocalOverride() && window.__JX_SPEAKER_PROBE_OVERRIDE__) {
    return window.__JX_SPEAKER_PROBE_OVERRIDE__();
  }
  return play();
}

export async function runLiveKitDeviceProbe(
  signal?: AbortSignal,
): Promise<LiveKitDeviceProbeResult> {
  throwIfAborted(signal);
  if (canUseLocalOverride() && window.__JX_DEVICE_PROBE_OVERRIDE__) {
    const result = await window.__JX_DEVICE_PROBE_OVERRIDE__();
    throwIfAborted(signal);
    return result;
  }
  const [{ Room, RoomEvent, Track, createLocalAudioTrack }, token] = await Promise.all([
    import('livekit-client'),
    roomsApi.liveKitProbeToken(),
  ]);
  const room = new Room({ adaptiveStream: false, dynacast: false });
  let track: Awaited<ReturnType<typeof createLocalAudioTrack>> | null = null;
  const abort = () => void room.disconnect();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    await room.connect(token.server_url, token.participant_token, { autoSubscribe: false });
    throwIfAborted(signal);
    track = await createLocalAudioTrack({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    await room.localParticipant.publishTrack(track, { source: Track.Source.Microphone });
    const microphoneSample = captureMicrophoneSample(track.mediaStreamTrack, signal);
    const rttSamples: number[] = [];
    const lossSamples: number[] = [];
    const qualitySamples: string[] = [];
    const qualityListener = (quality: unknown) => qualitySamples.push(String(quality));
    room.localParticipant.on(RoomEvent.ConnectionQualityChanged, qualityListener);
    try {
      for (let sample = 0; sample < 6; sample += 1) {
        await delay(500, signal);
        const stats = await track.getSenderStats();
        if (stats?.roundTripTime !== undefined) rttSamples.push(stats.roundTripTime * 1000);
        if (stats?.packetsLost !== undefined && stats.packetsSent !== undefined) {
          const lost = Math.max(0, stats.packetsLost);
          const total = Math.max(0, stats.packetsSent) + lost;
          if (total > 0) lossSamples.push((lost / total) * 100);
        }
      }
    } finally {
      room.localParticipant.off(RoomEvent.ConnectionQualityChanged, qualityListener);
    }
    const rttP95Ms = percentile95(rttSamples);
    const packetLossP95Percent = percentile95(lossSamples);
    const { inputPeak, recordingBlob } = await microphoneSample;
    return {
      status: combineStatus(
        classifyDeviceProbe(rttP95Ms, packetLossP95Percent),
        classifyInputLevel(inputPeak),
      ),
      rttP95Ms,
      packetLossP95Percent,
      connectionQuality:
        qualitySamples.at(-1) ?? String(room.localParticipant.connectionQuality ?? 'unknown'),
      samples: Math.max(rttSamples.length, lossSamples.length),
      inputPeak,
      recordingBlob,
    };
  } finally {
    signal?.removeEventListener('abort', abort);
    if (track) {
      await room.localParticipant.unpublishTrack(track);
      track.stop();
    }
    await room.disconnect();
  }
}
