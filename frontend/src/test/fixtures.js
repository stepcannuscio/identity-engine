export function createPrivacy(overrides = {}) {
  return {
    execution_mode: 'local',
    routing_enforced: true,
    warning_present: false,
    provider_label: 'Local model',
    model_label: 'llama3.1:8b',
    summary: 'Processed locally with privacy rules applied.',
    ...overrides,
  }
}

export function createDomain(overrides = {}) {
  return {
    domain: 'personality',
    attribute_count: 1,
    ...overrides,
  }
}

export function createAttribute(overrides = {}) {
  return {
    id: 'attribute-1',
    domain: 'personality',
    label: 'default_trait',
    value: 'Reflective and deliberate',
    elaboration: 'Takes time to think before responding.',
    confidence: 0.7,
    mutability: 'evolving',
    routing: 'local_only',
    source: 'reflection',
    last_confirmed: '2026-04-16T12:00:00Z',
    ...overrides,
  }
}

export function createSession(overrides = {}) {
  return {
    id: 'session-1',
    session_type: 'freeform',
    summary: '2 queries across session',
    attributes_created: 1,
    attributes_updated: 0,
    external_calls_made: 0,
    started_at: '2026-04-16T12:00:00Z',
    ended_at: '2026-04-16T12:05:00Z',
    privacy: createPrivacy(),
    routing_log: [],
    ...overrides,
  }
}
