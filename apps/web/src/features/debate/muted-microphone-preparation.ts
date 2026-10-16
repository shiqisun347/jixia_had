interface MicrophoneTrack {
  mute(): Promise<unknown>;
  stop(): void;
}

interface MicrophonePublisher<Track extends MicrophoneTrack> {
  currentTrack(): Track | undefined;
  createTracks(): Promise<Track[]>;
  publish(track: Track): Promise<unknown>;
  unpublish(track: Track): Promise<unknown>;
}

/** A real, muted publication lets Core prepare its reader without broadcasting speech. */
export function mutedMicrophonePreparation<Track extends MicrophoneTrack>(
  publisher: MicrophonePublisher<Track>,
) {
  let closed = false;
  let pending: Promise<void> | null = null;
  return {
    prepare(): Promise<void> {
      if (closed) return Promise.reject(new Error('microphone_session_closed'));
      if (pending) return pending;
      const prepare = async () => {
        const current = publisher.currentTrack();
        if (current) {
          await current.mute();
          if (closed) throw new Error('microphone_session_closed');
          return;
        }
        const tracks = await publisher.createTracks();
        let publishAttempted = false;
        try {
          if (tracks.length !== 1) throw new Error('microphone_track_required');
          const track = tracks[0]!;
          await track.mute();
          if (closed) throw new Error('microphone_session_closed');
          publishAttempted = true;
          await publisher.publish(track);
          if (closed) throw new Error('microphone_session_closed');
        } catch (error) {
          if (publishAttempted && tracks[0])
            await publisher.unpublish(tracks[0]).catch(() => undefined);
          for (const track of tracks) track.stop();
          throw error;
        }
      };
      pending = prepare().finally(() => {
        pending = null;
      });
      return pending;
    },
    close() {
      // Room.disconnect owns already published tracks. This flag also fences
      // a permission or publication operation that returns after disconnect.
      closed = true;
    },
  };
}
