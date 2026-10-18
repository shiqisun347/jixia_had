// Event-specific identities and uploaded portraits are private deployment data.
const identities = Object.freeze({});
export function avatarUrl(participant) {
  const identity = identities[participant.id];
  return identity &&
    identity.name.replace(/\s+/g, '') === participant.name.replace(/\s+/g, '') &&
    identity.kind === participant.kind
    ? identity.url
    : `/assets/${participant.avatar}.webp`;
}
