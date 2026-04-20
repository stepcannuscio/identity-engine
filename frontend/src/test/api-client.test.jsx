import client, {
  DEFAULT_REQUEST_TIMEOUT_MS,
  SLOW_REQUEST_TIMEOUT_MS,
  withSlowRequestTimeout,
} from '../api/client.js'
import {
  answerTeachQuestion,
  capturePreview,
  getAuthStatus,
  uploadArtifact,
} from '../api/endpoints.js'

describe('api client', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    sessionStorage.clear()
  })

  function withStubbedWindow(reload = vi.fn()) {
    const originalWindow = globalThis.window
    const stubbedWindow = {
      ...originalWindow,
      location: {
        ...originalWindow.location,
        reload,
      },
    }

    vi.stubGlobal('window', stubbedWindow)

    return reload
  }

  it('adds the session token to outgoing request headers', async () => {
    sessionStorage.setItem('session_token', 'session-token')

    const interceptor = client.interceptors.request.handlers[0].fulfilled
    const config = await interceptor({ headers: {} })

    expect(config.headers.Authorization).toBe('Bearer session-token')
  })

  it('clears the session and reloads on unauthorized responses', async () => {
    sessionStorage.setItem('session_token', 'session-token')
    const reload = withStubbedWindow()
    const interceptor = client.interceptors.response.handlers[0].rejected

    await expect(
      interceptor({
        response: { status: 401 },
      }),
    ).rejects.toEqual({
      response: { status: 401 },
    })

    expect(sessionStorage.getItem('session_token')).toBeNull()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('leaves non-401 errors alone', async () => {
    sessionStorage.setItem('session_token', 'session-token')
    const reload = withStubbedWindow()
    const interceptor = client.interceptors.response.handlers[0].rejected

    await expect(
      interceptor({
        response: { status: 500 },
      }),
    ).rejects.toEqual({
      response: { status: 500 },
    })

    expect(sessionStorage.getItem('session_token')).toBe('session-token')
    expect(reload).not.toHaveBeenCalled()
  })

  it('uses the default timeout for ordinary requests', async () => {
    const getSpy = vi.spyOn(client, 'get').mockResolvedValue({ data: { ok: true } })

    await getAuthStatus()

    expect(getSpy).toHaveBeenCalledWith('/auth/status')
    expect(client.defaults.timeout).toBe(DEFAULT_REQUEST_TIMEOUT_MS)
  })

  it('uses the slow timeout for teach answer extraction', async () => {
    const postSpy = vi.spyOn(client, 'post').mockResolvedValue({ data: { ok: true } })

    await answerTeachQuestion('question-1', { answer: 'Long-form answer.' })

    expect(postSpy).toHaveBeenCalledWith(
      '/teach/questions/question-1/answer',
      { answer: 'Long-form answer.' },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('uses the slow timeout for capture preview requests', async () => {
    const postSpy = vi.spyOn(client, 'post').mockResolvedValue({ data: { proposed: [] } })

    await capturePreview('A detailed note', 'values', true)

    expect(postSpy).toHaveBeenCalledWith(
      '/capture/preview',
      {
        text: 'A detailed note',
        domain_hint: 'values',
        allow_external_extraction: true,
      },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('uses the slow timeout for artifact uploads', async () => {
    const postSpy = vi.spyOn(client, 'post').mockResolvedValue({ data: { artifact_id: 'artifact-1' } })

    await uploadArtifact({
      text: 'Large note body',
      title: 'Notebook',
      type: null,
      source: null,
      domain: null,
      metadata: null,
      tags: ['notes'],
    })

    expect(postSpy).toHaveBeenCalledWith(
      '/artifacts',
      {
        text: 'Large note body',
        title: 'Notebook',
        type: null,
        source: null,
        domain: null,
        metadata: null,
        tags: ['notes'],
      },
      { timeout: SLOW_REQUEST_TIMEOUT_MS },
    )
  })

  it('builds a slow-timeout config helper', () => {
    expect(withSlowRequestTimeout()).toEqual({ timeout: SLOW_REQUEST_TIMEOUT_MS })
    expect(withSlowRequestTimeout({ headers: { 'X-Test': '1' } })).toEqual({
      headers: { 'X-Test': '1' },
      timeout: SLOW_REQUEST_TIMEOUT_MS,
    })
  })
})
