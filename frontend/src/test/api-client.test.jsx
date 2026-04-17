import client from '../api/client.js'

describe('api client', () => {
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
})
