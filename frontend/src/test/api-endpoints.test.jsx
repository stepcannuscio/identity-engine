import client, { SLOW_REQUEST_TIMEOUT_MS } from '../api/client.js'
import {
  login,
  logout,
  getAttributes,
  getAttribute,
  updateAttribute,
  correctAttribute,
  retractAttribute,
  createAttribute,
  confirmAttribute,
  getDomains,
  capture,
  createPreferenceSignal,
  previewInterview,
  saveInterview,
  uploadArtifact,
  getSessions,
  submitQueryFeedback,
  getCurrentSession,
  getTeachBootstrap,
  getTeachQuestions,
  feedbackTeachQuestion,
  startReflection,
  submitReflectionTurn,
  getSetupModelOptions,
  saveProviderCredentials,
  saveSetupProfile,
  updateSecurityCheckOverride,
  getSecurityPosture,
  configurePrivateServer,
  testPrivateServerConnection,
} from '../api/endpoints.js'

describe('api endpoints', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('logs in with a passphrase', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { session_token: 'tok' } })
    const result = await login('secret')
    expect(client.post).toHaveBeenCalledWith('/auth/login', { passphrase: 'secret' })
    expect(result).toEqual({ session_token: 'tok' })
  })

  it('logs out', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await logout()
    expect(client.post).toHaveBeenCalledWith('/auth/logout')
  })

  it('fetches attributes without domain filter', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: [] })
    await getAttributes()
    expect(client.get).toHaveBeenCalledWith('/attributes', { params: undefined })
  })

  it('fetches attributes with domain filter', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: [] })
    await getAttributes('values')
    expect(client.get).toHaveBeenCalledWith('/attributes', { params: { domain: 'values' } })
  })

  it('fetches a single attribute by id', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { id: 'attr-1' } })
    await getAttribute('attr-1')
    expect(client.get).toHaveBeenCalledWith('/attributes/attr-1')
  })

  it('updates an attribute', async () => {
    vi.spyOn(client, 'put').mockResolvedValue({ data: { id: 'attr-1' } })
    await updateAttribute('attr-1', { value: 'updated' })
    expect(client.put).toHaveBeenCalledWith('/attributes/attr-1', { value: 'updated' })
  })

  it('corrects an attribute', async () => {
    vi.spyOn(client, 'patch').mockResolvedValue({ data: { id: 'attr-1' } })
    await correctAttribute('attr-1', { corrected_value: 'fixed' })
    expect(client.patch).toHaveBeenCalledWith('/attributes/attr-1', { corrected_value: 'fixed' })
  })

  it('retracts an attribute', async () => {
    vi.spyOn(client, 'delete').mockResolvedValue({ data: { ok: true } })
    await retractAttribute('attr-1')
    expect(client.delete).toHaveBeenCalledWith('/attributes/attr-1')
  })

  it('creates an attribute', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { id: 'new-attr' } })
    await createAttribute({ value: 'I value honesty', domain: 'values' })
    expect(client.post).toHaveBeenCalledWith('/attributes', { value: 'I value honesty', domain: 'values' })
  })

  it('confirms an attribute', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await confirmAttribute('attr-1')
    expect(client.post).toHaveBeenCalledWith('/attributes/attr-1/confirm')
  })

  it('fetches all domains', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: ['values', 'goals'] })
    const result = await getDomains()
    expect(client.get).toHaveBeenCalledWith('/domains')
    expect(result).toEqual(['values', 'goals'])
  })

  it('submits a capture with accepted ids', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await capture('my note', 'values', ['attr-1'], false)
    expect(client.post).toHaveBeenCalledWith(
      '/capture',
      { text: 'my note', domain_hint: 'values', accepted: ['attr-1'], allow_external_extraction: false },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('submits a capture with null accepted and no domain hint', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await capture('my note', null, null, false)
    expect(client.post).toHaveBeenCalledWith(
      '/capture',
      { text: 'my note', domain_hint: null, accepted: null, allow_external_extraction: false },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('creates a preference signal', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await createPreferenceSignal({ type: 'like', attribute_id: 'attr-1' })
    expect(client.post).toHaveBeenCalledWith('/preferences/signals', { type: 'like', attribute_id: 'attr-1' })
  })

  it('previews an interview answer', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { proposed: [] } })
    await previewInterview('values', 'What matters most?', 'Family', false)
    expect(client.post).toHaveBeenCalledWith(
      '/interview/preview',
      { domain: 'values', question: 'What matters most?', answer: 'Family', allow_external_extraction: false },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('saves an interview answer', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await saveInterview('values', 'What matters most?', 'Family', ['attr-1'], false)
    expect(client.post).toHaveBeenCalledWith(
      '/interview',
      { domain: 'values', question: 'What matters most?', answer: 'Family', accepted: ['attr-1'], allow_external_extraction: false },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('uploads an artifact via FormData when a file is provided', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { artifact_id: 'art-1' } })
    const file = new File(['content'], 'notes.txt', { type: 'text/plain' })
    await uploadArtifact({ file, title: 'My Notes', type: 'note', source: 'web', domain: 'work', metadata: { key: 'v' }, tags: ['tag1'] })
    const [path, body, config] = client.post.mock.calls[0]
    expect(path).toBe('/artifacts')
    expect(body).toBeInstanceOf(FormData)
    expect(config).toEqual({ timeout: SLOW_REQUEST_TIMEOUT_MS })
  })

  it('uploads an artifact via FormData with no optional fields', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { artifact_id: 'art-2' } })
    const file = new File(['content'], 'notes.txt', { type: 'text/plain' })
    await uploadArtifact({ file })
    const [path, body, config] = client.post.mock.calls[0]
    expect(path).toBe('/artifacts')
    expect(body).toBeInstanceOf(FormData)
    expect(config).toEqual({ timeout: SLOW_REQUEST_TIMEOUT_MS })
  })

  it('fetches all sessions', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: [] })
    await getSessions()
    expect(client.get).toHaveBeenCalledWith('/sessions')
  })

  it('submits query feedback', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await submitQueryFeedback({ message_id: 'msg-1', rating: 'good' })
    expect(client.post).toHaveBeenCalledWith('/query/feedback', { message_id: 'msg-1', rating: 'good' })
  })

  it('fetches the current session', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { id: 'session-1' } })
    await getCurrentSession()
    expect(client.get).toHaveBeenCalledWith('/sessions/current')
  })

  it('fetches teach bootstrap data', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { profiles: [] } })
    await getTeachBootstrap()
    expect(client.get).toHaveBeenCalledWith('/teach/bootstrap')
  })

  it('fetches teach questions', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: [] })
    await getTeachQuestions()
    expect(client.get).toHaveBeenCalledWith('/teach/questions')
  })

  it('submits feedback on a teach question', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await feedbackTeachQuestion('q-1', 'too easy')
    expect(client.post).toHaveBeenCalledWith('/teach/questions/q-1/feedback', { feedback: 'too easy' })
  })

  it('starts a reflection session', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { session_id: 'refl-1' } })
    await startReflection()
    expect(client.post).toHaveBeenCalledWith(
      '/teach/reflection/start',
      {},
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('submits a reflection turn', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { response: 'interesting' } })
    await submitReflectionTurn('refl-1', 'My thought')
    expect(client.post).toHaveBeenCalledWith(
      '/teach/reflection/turn',
      { session_id: 'refl-1', user_message: 'My thought' },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('fetches setup model options', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { providers: [] } })
    await getSetupModelOptions()
    expect(client.get).toHaveBeenCalledWith('/setup/model-options')
  })

  it('saves provider credentials', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await saveProviderCredentials('openai', { api_key: 'sk-test' }) // pragma: allowlist secret
    expect(client.post).toHaveBeenCalledWith('/setup/providers/openai/credentials', {
      credentials: { api_key: 'sk-test' }, // pragma: allowlist secret
    })
  })

  it('saves setup profile', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await saveSetupProfile({ profile: 'balanced_hybrid', privacy_preference: 'balanced' })
    expect(client.post).toHaveBeenCalledWith('/setup/profile', {
      profile: 'balanced_hybrid',
      privacy_preference: 'balanced',
    })
  })

  it('updates a security check override', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await updateSecurityCheckOverride('disk_encryption', { completed: true })
    expect(client.post).toHaveBeenCalledWith(
      '/setup/security-posture/checks/disk_encryption',
      { completed: true },
    )
  })

  it('fetches security posture', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { score: 80, checks: [] } })
    await getSecurityPosture()
    expect(client.get).toHaveBeenCalledWith('/setup/security-posture')
  })

  it('configures a private server with model', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await configurePrivateServer('http://myserver:11434', 'llama3')
    expect(client.post).toHaveBeenCalledWith('/setup/providers/private_server/configure', {
      server_url: 'http://myserver:11434',
      model: 'llama3',
    })
  })

  it('configures a private server without model', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })
    await configurePrivateServer('http://myserver:11434')
    expect(client.post).toHaveBeenCalledWith('/setup/providers/private_server/configure', {
      server_url: 'http://myserver:11434',
      model: undefined,
    })
  })

  it('tests a private server connection', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { reachable: true, latency_ms: 42 } })
    const result = await testPrivateServerConnection('http://myserver:11434', 'llama3')
    expect(client.post).toHaveBeenCalledWith('/setup/providers/private_server/test', {
      server_url: 'http://myserver:11434',
      model: 'llama3',
    })
    expect(result).toEqual({ reachable: true, latency_ms: 42 })
  })

  it('tests a private server connection without a model', async () => {
    vi.spyOn(client, 'post').mockResolvedValue({ data: { reachable: false } })
    await testPrivateServerConnection('http://myserver:11434')
    expect(client.post).toHaveBeenCalledWith('/setup/providers/private_server/test', {
      server_url: 'http://myserver:11434',
      model: undefined,
    })
  })
})
