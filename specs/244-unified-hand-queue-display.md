# 244 Unified hand queue display

- Version: v2.0
- Status: Implemented locally; production release pending
- Date: 2026-09-01

## Scope

The candidate panel displays the authoritative `team_hand_queue` as one
sequence. Human and Agent candidates that have entered the queue show `第 N
名`; a selected candidate additionally shows `将发言`. Agents still deciding,
skipping, or selected through fallback retain those state labels when no queue
rank exists.

The ordering remains server-owned: humans are accepted FIFO and have priority;
Agent ranks are appended after human ranks according to Core's deterministic
selection rules. No client-side timestamp or reordering is introduced.

## Acceptance

1. A human candidate never uses `已举手` as the primary queue label and shows a
   concrete rank.
2. Human and Agent candidates use the same rank sequence when both are visible.
3. Decision states without a rank remain distinguishable and do not receive a
   fabricated rank.
4. Existing privacy projection remains unchanged: only authorized teammates
   and administrators see the team queue.

## Rollback

Revert the Web presentation change only. Core queue state, selection semantics,
and persisted audit events remain unchanged.
